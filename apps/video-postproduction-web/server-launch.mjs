// Production start command for the web image (SPEC-001 R-PROXY-2, R-TS-5). Logic lives in server/launch.mjs.
import http from "node:http";

import {launch} from "./server/launch.mjs";

const code = await launch({env: process.env, http, importServer: () => import("./server.js")});
if (code !== 0) process.exit(code);
