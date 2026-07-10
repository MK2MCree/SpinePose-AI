import { encodePath, proxyToMindSpine } from "../../_lib/proxy";

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyToMindSpine(request, `/health/${encodePath(path)}`);
}
