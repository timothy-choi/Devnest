import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, backendReachabilityUserDetail, readBackendJson } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type RegisterOk = {
  user_auth_id: number;
  username: string;
  email: string;
  created_at: string;
};

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

  let registerResponse: Awaited<ReturnType<typeof backendRequest>>;
  let registerData: RegisterOk | { detail: string } | null;
  try {
    registerResponse = await backendRequest({
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
    registerData = await readBackendJson<RegisterOk | { detail: string }>(registerResponse);
  } catch (err) {
    res.status(503).json({
      detail: `Could not reach the API: ${backendReachabilityUserDetail(err)}`,
    });
    return;
  }

  if (!registerResponse.ok) {
    forwardJson(res, registerResponse.status, registerData);
    return;
  }

  const created = registerData as RegisterOk;

  res.status(201).json({
    message: "Account created successfully. Please log in.",
    username: created.username,
    email: created.email,
  });
}
