from dotenv import load_dotenv

load_dotenv()

from inbox import fetch_unseen

mails = fetch_unseen(mark_seen=False)
print(len(mails), "unseen mails")
for m in mails:
    print(m["sender"], "|", m["subject"], "| authenticated:", m["authenticated"])
    print("   header:", m["auth"])