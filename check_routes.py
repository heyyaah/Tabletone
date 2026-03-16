import re

content = open('app.py', encoding='utf-8').read()
lines = content.split('\n')

# Find line number of /send/voice
for i, line in enumerate(lines, 1):
    if '/send/voice' in line:
        print(f"Line {i}: {line.strip()}")

# Check what's between line 2700-2730 (around the route)
print("\n--- Context around send_voice ---")
for i, line in enumerate(lines, 1):
    if 2715 <= i <= 2730:
        print(f"{i}: {line}")
