const HOP_BY_HOP_HEADERS = [
  "connection",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

function backendBaseUrl(): string | null {
  const configured = process.env.MINDSPINE_BACKEND_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  return process.env.NODE_ENV === "development" ? "http://127.0.0.1:8765" : null;
}

export async function proxyToMindSpine(request: Request, upstreamPath: string): Promise<Response> {
  const baseUrl = backendBaseUrl();
  if (!baseUrl) {
    return Response.json(
      { code: 503, message: "模型计算服务尚未配置，请联系管理员" },
      { status: 503 },
    );
  }

  const incomingUrl = new URL(request.url);
  const target = new URL(upstreamPath, `${baseUrl}/`);
  target.search = incomingUrl.search;

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
  headers.delete("host");
  headers.set("x-forwarded-host", incomingUrl.host);
  headers.set("x-forwarded-proto", incomingUrl.protocol.replace(":", ""));
  const proxySecret = process.env.MINDSPINE_PROXY_SECRET?.trim();
  if (proxySecret) headers.set("x-mindspine-proxy-secret", proxySecret);

  const method = request.method.toUpperCase();
  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers,
    redirect: "manual",
  };
  if (method !== "GET" && method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  try {
    const upstream = await fetch(target, init);
    const responseHeaders = new Headers(upstream.headers);
    for (const name of HOP_BY_HOP_HEADERS) responseHeaders.delete(name);
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { code: 503, message: "模型计算服务暂时不可用，请稍后重试" },
      { status: 503 },
    );
  }
}

export function encodePath(parts: string[]): string {
  return parts.map((part) => encodeURIComponent(part)).join("/");
}

