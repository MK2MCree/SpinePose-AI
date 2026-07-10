import { encodePath, proxyToMindSpine } from "../../_lib/proxy";

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return proxyToMindSpine(request, `/temp/${encodePath(path)}`);
}

