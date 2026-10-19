import asyncio
from dotenv import load_dotenv

load_dotenv()

from llm import extract

print(asyncio.run(extract("Flight to Delhi on 3 Nov at 6 AM")))