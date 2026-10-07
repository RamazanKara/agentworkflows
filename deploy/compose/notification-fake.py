"""Local Slack/webhook sink and SMTP catcher. Trial messages never leave Compose."""

import asyncio
from email import policy
from email.parser import BytesParser

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
messages: list[dict] = []


@app.get("/")
async def inbox():
    return {"messages": messages}


@app.post("/{channel}")
async def receive(channel: str, request: Request):
    messages.append({"channel": channel, "body": await request.json()})
    del messages[:-256]
    return {"ok": True}


async def smtp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    writer.write(b"220 Compose SMTP catcher\r\n")
    try:
        while line := await reader.readline():
            command = line.split(b" ", 1)[0].strip().upper()
            if command == b"QUIT":
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            if command == b"DATA":
                writer.write(b"354 End with a dot\r\n")
                await writer.drain()
                body = bytearray()
                while (line := await reader.readline()) not in {b".\r\n", b""}:
                    body.extend(line[1:] if line.startswith(b"..") else line)
                    if len(body) > 262144:
                        return
                message = BytesParser(policy=policy.default).parsebytes(body)
                messages.append(
                    {
                        "channel": "email",
                        "subject": str(message["Subject"]),
                        "body": message.get_content(),
                        "message_id": str(message["Message-ID"]),
                    }
                )
                del messages[:-256]
            writer.write(b"250 OK\r\n")
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    server = await asyncio.start_server(smtp, "0.0.0.0", 1025)
    async with server:
        await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8025, log_level="warning")).serve()


if __name__ == "__main__":
    asyncio.run(main())
