import { NextRequest, NextResponse } from "next/server";

export async function middleware(req: NextRequest) {
  const cookie = req.cookies.get("beat-planner-session");
  if (!cookie?.value) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*"],
};
