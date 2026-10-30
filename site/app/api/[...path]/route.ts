import { encodePath, proxyToMindSpine } from "../../_lib/proxy";

type RouteContext = { params: Promise<{ path: string[] }> };

async function handler(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return proxyToMindSpine(request, `/api/${encodePath(path)}`);
}

export { handler as GET, handler as POST, handler as PUT, handler as DELETE, handler as OPTIONS };
