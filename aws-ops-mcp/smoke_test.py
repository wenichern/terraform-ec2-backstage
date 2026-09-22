import asyncio, sys
from fastmcp import Client

async def main(url):
    async with Client(url) as c:
        tools = await c.list_tools()
        print("Tools:", [t.name for t in tools])
        r = await c.call_tool("list_outdated_ec2", {})
        print(r.content[0].text)

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9002/mcp"))
