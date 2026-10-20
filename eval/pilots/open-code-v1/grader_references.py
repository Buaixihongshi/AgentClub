"""Author-written solutions for grader validation only; never provide these to agents."""

REFERENCES = {
    "abc387_c": '''import sys
def count(n):
    if n < 10:
        return 0
    digits = list(map(int, str(n)))
    size = len(digits)
    total = sum(first ** (length - 1) for length in range(2, size) for first in range(1, 10))
    total += sum(first ** (size - 1) for first in range(1, digits[0]))
    first = digits[0]
    for i, digit in enumerate(digits[1:], 1):
        total += min(first, digit) * first ** (size - i - 1)
        if digit >= first:
            break
    else:
        total += 1
    return total
left, right = map(int, sys.stdin.read().split())
print(count(right) - count(left - 1))
''',
    "abc391_e": '''import sys
data = sys.stdin.read().split()
costs = [(int(bit != "0"), int(bit != "1")) for bit in data[1]]
while len(costs) > 1:
    next_costs = []
    for i in range(0, len(costs), 3):
        group = costs[i:i + 3]
        next_costs.append(tuple(sum(sorted(item[target] for item in group)[:2]) for target in (0, 1)))
    costs = next_costs
print(max(costs[0]))
''',
    "abc400_b": '''import sys
n, m = map(int, sys.stdin.read().split())
total, term = 1, 1
for _ in range(m):
    term *= n
    total += term
    if total > 10 ** 9:
        print("inf")
        break
else:
    print(total)
''',
    "abc394_d": '''import sys
stack = []
matching = {')': '(', ']': '[', '>': '<'}
valid = True
for char in sys.stdin.read().strip():
    if char in "([<":
        stack.append(char)
    elif not stack or stack.pop() != matching[char]:
        valid = False
        break
print("Yes" if valid and not stack else "No")
''',
    "abc396_d": '''import sys
values = iter(map(int, sys.stdin.read().split()))
n, m = next(values), next(values)
edges = [[] for _ in range(n)]
for _ in range(m):
    u, v, w = next(values) - 1, next(values) - 1, next(values)
    edges[u].append((v, w))
    edges[v].append((u, w))
best = 1 << 61
def search(node, mask, value):
    global best
    if node == n - 1:
        best = min(best, value)
        return
    for neighbor, weight in edges[node]:
        if not mask & (1 << neighbor):
            search(neighbor, mask | (1 << neighbor), value ^ weight)
search(0, 1, 0)
print(best)
''',
    "abc395_e": '''import sys
from heapq import heappush, heappop
values = iter(map(int, sys.stdin.read().split()))
n, m, flip = next(values), next(values), next(values)
forward, backward = [[] for _ in range(n)], [[] for _ in range(n)]
for _ in range(m):
    u, v = next(values) - 1, next(values) - 1
    forward[u].append(v)
    backward[v].append(u)
distances = [10 ** 30] * (2 * n)
distances[0] = 0
queue = [(0, 0)]
while queue:
    cost, state = heappop(queue)
    if cost != distances[state]:
        continue
    layer, node = divmod(state, n)
    if node == n - 1:
        print(cost)
        break
    swapped = (1 - layer) * n + node
    if cost + flip < distances[swapped]:
        distances[swapped] = cost + flip
        heappush(queue, (cost + flip, swapped))
    for neighbor in (forward if layer == 0 else backward)[node]:
        target = layer * n + neighbor
        if cost + 1 < distances[target]:
            distances[target] = cost + 1
            heappush(queue, (cost + 1, target))
''',
}

# This deliberately incorrect implementation agrees with the two public
# majority examples but loses the tree structure. It is diagnostic, not input
# supplied to a collaboration arm and not a criterion for selecting tasks.
MUTANTS = {
    "abc391_e_flat_majority": ("abc391_e", '''import sys
parts = sys.stdin.read().split()
bits = parts[1]
ones = bits.count("1")
half = len(bits) // 2
print(ones - half if ones > half else half + 1 - ones)
'''),
}
