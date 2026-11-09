//! The same workload as the Python measurement: five bots long-polling the Bot API,
//! answering /start and /help in en/uz/ru, and writing who was active to Postgres in
//! batches (as the Python bots' record_activity_batch does). Memory floor of a Rust
//! runtime for MumuBots, not a port of any bot.
use serde::Deserialize;
use serde_json::json;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;

#[derive(Deserialize)]
struct Resp<T> { ok: bool, result: Option<T> }
#[derive(Deserialize)]
struct Update { update_id: i64, message: Option<Message> }
#[derive(Deserialize)]
struct Message { chat: Chat, from: Option<User>, text: Option<String> }
#[derive(Deserialize)]
struct Chat { id: i64 }
#[derive(Deserialize)]
struct User { id: i64, language_code: Option<String> }

fn text(lang: &str, key: &str, bot: &str) -> String {
    let t: &[(&str, &str, &str)] = &[
        ("en", "start", "Hi! I'm {bot}. Send me something to begin."),
        ("uz", "start", "Salom! Men {bot}. Boshlash uchun biror narsa yuboring."),
        ("ru", "start", "Привет! Я {bot}. Отправьте что-нибудь, чтобы начать."),
        ("en", "help", "{bot}: /start, /help, /privacy, /terms, /deletemydata"),
        ("uz", "help", "{bot}: /start, /help, /privacy, /terms, /deletemydata"),
        ("ru", "help", "{bot}: /start, /help, /privacy, /terms, /deletemydata"),
    ];
    let lang = if ["en", "uz", "ru"].contains(&lang) { lang } else { "en" };
    t.iter().find(|(l, k, _)| *l == lang && *k == key).map(|(_, _, s)| s.replace("{bot}", bot)).unwrap_or_default()
}

async fn run_bot(http: reqwest::Client, api: String, token: String, name: String, schema: String,
                 seen: Arc<Mutex<HashMap<String, HashSet<i64>>>>) {
    let base = format!("{api}/bot{token}");
    let mut offset: i64 = 0;
    loop {
        let resp = http.post(format!("{base}/getUpdates"))
            .json(&json!({"offset": offset, "timeout": 10})).send().await;
        let updates: Vec<Update> = match resp {
            Ok(r) => match r.json::<Resp<Vec<Update>>>().await { Ok(v) if v.ok => v.result.unwrap_or_default(), _ => vec![] },
            Err(_) => { tokio::time::sleep(Duration::from_secs(1)).await; vec![] }
        };
        for u in updates {
            offset = offset.max(u.update_id + 1);
            let Some(m) = u.message else { continue };
            let Some(text_in) = m.text.as_deref() else { continue };
            let lang = m.from.as_ref().and_then(|f| f.language_code.clone()).unwrap_or_else(|| "en".into());
            let key = match text_in.split_whitespace().next() { Some("/start") => "start", Some("/help") => "help", _ => continue };
            if let Some(f) = &m.from { seen.lock().await.entry(schema.clone()).or_default().insert(f.id); }
            let _ = http.post(format!("{base}/sendMessage"))
                .json(&json!({"chat_id": m.chat.id, "text": text(&lang, key, &name)})).send().await;
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let api = std::env::var("TELEGRAM_API_URL").unwrap_or_else(|_| "https://api.telegram.org".into());
    let db_url = std::env::var("DATABASE_URL").expect("DATABASE_URL");
    let (db, conn) = tokio_postgres::connect(&db_url, tokio_postgres::NoTls).await.expect("database");
    tokio::spawn(async move { let _ = conn.await; });
    let http = reqwest::Client::builder().timeout(Duration::from_secs(30)).pool_max_idle_per_host(2).build().unwrap();
    let seen: Arc<Mutex<HashMap<String, HashSet<i64>>>> = Arc::new(Mutex::new(HashMap::new()));
    let bots = [("SBOT", "StickerBot", "sticker_bot"), ("CBOT", "ConvertBot", "convert_bot"),
                ("DBOT", "DownloaderBot", "downloader_bot"), ("ABOT", "AnonBot", "anon_bot"),
                ("MBOT", "ManagerBot", "manager_bot")];
    for (prefix, name, schema) in bots {
        db.batch_execute(&format!("CREATE SCHEMA IF NOT EXISTS rs_{schema}; CREATE TABLE IF NOT EXISTS rs_{schema}.activity_events \
             (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())")).await.expect("schema");
        if let Ok(token) = std::env::var(format!("{prefix}_TOKEN")) {
            tokio::spawn(run_bot(http.clone(), api.clone(), token, name.into(), format!("rs_{schema}"), seen.clone()));
        }
    }
    eprintln!("polling");
    loop {   // the activity flush, every 30 s, one statement per bot
        tokio::time::sleep(Duration::from_secs(30)).await;
        let batch: Vec<(String, Vec<i64>)> = seen.lock().await.drain().map(|(k, v)| (k, v.into_iter().collect())).collect();
        for (schema, ids) in batch {
            let _ = db.execute(&format!("INSERT INTO {schema}.activity_events (user_id) SELECT unnest($1::bigint[])"), &[&ids]).await;
        }
    }
}
