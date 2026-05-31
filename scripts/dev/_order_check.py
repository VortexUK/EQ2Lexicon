import json
import sys
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from backend.census.config import SERVICE_ID  # noqa: E402

# Test c:sort=id:ASC - does it give stable, sequential ordering?
for start in [0, 100, 1000, 10000]:
    url = f"https://census.daybreakgames.com/s:{SERVICE_ID}/get/eq2/item?c:start={start}&c:limit=5&c:sort=id:ASC"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        items = data.get("item_list", [])
        print(f"c:start={start} (sorted by id ASC):")
        for item in items:
            print(f"  id={item['id']:>15,}  name={item['displayname']}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()
