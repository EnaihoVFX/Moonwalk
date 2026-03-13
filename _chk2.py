import ast
ast.parse(open('/Users/enaihouwaspaul/Moonwalk/backend/agent/core.py').read())
src = open('/Users/enaihouwaspaul/Moonwalk/backend/agent/core.py').read()
assert 'from tools.browser_tools import browser_store' not in src, 'Bad re-import still present'
assert '_gw_snap = browser_store.get_snapshot()' in src, 'Missing fixed snapshot call'
assert src.count('browser_store') > 4, 'browser_store references look off'
print('OK: core.py syntax + scoping fix verified')
