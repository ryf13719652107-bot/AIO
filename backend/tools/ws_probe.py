import asyncio, json
import websockets


async def main():
    url = "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade"
    print(f"connecting {url}")
    try:
        async with asyncio.timeout(20):
            async with websockets.connect(url, ping_interval=20) as ws:
                print("CONNECTED")
                for i in range(3):
                    msg = await ws.recv()
                    d = json.loads(msg)
                    print(f"frame {i}: stream={d.get('stream')} payload={d.get('data')}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


asyncio.run(main())
