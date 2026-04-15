import type { NextApiRequest, NextApiResponse } from "next";

import loginHandler from "@/pages/api/auth/login";
import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "POST") {
    sendMethodNotAllowed(res, ["POST"]);
    return;
  }

  const { username, email, password } = req.body as {
    username: string;
    email: string;
    password: string;
  };

  const registerResponse = await backendRequest({
    req,
    res,
    path: "/auth/register",
    method: "POST",
    body: {
      username,
      email,
      password,
    },
    authenticated: false,
    retryOnUnauthorized: false,
  });

  const registerData = await readBackendJson(registerResponse);

  if (!registerResponse.ok) {
    forwardJson(res, registerResponse.status, registerData);
    return;
  }

  req.body = {
    username,
    password,
  };

  await loginHandler(req, res);
}
