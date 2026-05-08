import type { SessionOptions } from "iron-session";

export interface SessionData {
  authenticated?: boolean;
}

export const sessionOptions: SessionOptions = {
  password: process.env.SESSION_SECRET || "complex_password_at_least_32_characters_long!!",
  cookieName: "beat-planner-session",
  cookieOptions: {
    secure: process.env.NODE_ENV === "production",
  },
};
