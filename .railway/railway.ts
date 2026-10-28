// Railway Infrastructure as Code: the replacement for railway.json, which Railway stops
// reading on 2026-12-01.
//
// THIS FILE IS A DRAFT TO MERGE, NOT TO APPLY AS IT IS. It describes the whole project:
// on `railway config apply`, any service, volume or variable that is not in it is deleted --
// the volume holding the data key (DATA_KEY_FILE) included. So:
//
//   1. railway link                     (pick the bots' project and environment)
//   2. railway config pull --force      (writes the live project into this file)
//   3. carry over from this draft only the `build` and `deploy` blocks below, into the
//      service that runs main.py; keep every volume, variable and other service that pull
//      wrote, and keep secrets as preserve()
//   4. delete railway.json (Railway refuses to plan a service still managed by it)
//   5. railway config plan              (it must show only build/deploy settings changing)
//   6. railway config apply
//
// `railway config migrate` does steps 2-4 in one go and is the safer route if this draft and
// the live project disagree. The keys match railway.json one for one.
import { defineRailway, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const bots = service("mumu-bots", {
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      sleepApplication: false,
      overlapSeconds: 30,
      drainingSeconds: 120,
    },
    replicas: 1,
    env: {
      DATABASE_URL: preserve(),
      // The sealed data key (docs/data-key-options.md). Once DATA_KEY is set, never drop this
      // line: apply deletes variables the file doesn't list, and a sealed value cannot be read back.
      DATA_KEY: preserve(),
    },
  });

  // The three-service split runs the same image three times, each with its own BOTS:
  //   service("mumu-convert",    { ...same build/deploy..., env: { BOTS: "convert" } })
  //   service("mumu-downloader", { ...same build/deploy..., env: { BOTS: "downloader" } })
  //   service("mumu-family",     { ...same build/deploy..., env: { BOTS: "sticker,anon,manager" } })
  // One token may be polled by one service only, and each service needs the data key file.

  return project("mumu-bots", { resources: [bots] });
});
