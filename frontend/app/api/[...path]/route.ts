import { NextRequest } from "next/server";
import { correlationId, validRequestId } from "../../../lib/request-id";

export const dynamic = "force-dynamic";
async function proxy(request: NextRequest, context: { params: Promise<{path: string[]}> }) {
  const { path } = await context.params;
  const requestId=correlationId(request.headers.get("X-Request-ID"));
  if (path[0] !== "v1") return Response.json({detail: "Unknown API path"}, {status: 404,headers:{"X-Request-ID":requestId}});
  const base = process.env.API_BASE_URL || "http://127.0.0.1:8001";
  const url = `${base.replace(/\/$/, "")}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  try {
    const upstream = await fetch(url, {
      method: request.method, headers: {"Content-Type": "application/json","X-Request-ID":requestId},
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store", signal: AbortSignal.timeout(180000),
    });
    return new Response(await upstream.text(), {status: upstream.status,
      headers: {"Content-Type": upstream.headers.get("Content-Type") || "application/json","X-Request-ID":validRequestId(upstream.headers.get("X-Request-ID")) || requestId}});
  } catch {
    return Response.json({detail: "Backend недоступен или не успел ответить. Проверьте запуск API на порту 8001 и подключение к БД."}, {status: 502,headers:{"X-Request-ID":requestId}});
  }
}
export { proxy as GET, proxy as POST, proxy as PUT };
