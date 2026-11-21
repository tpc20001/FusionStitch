"""🤝 程式握手（apphs/1，規則第 3 版）：同一台電腦上的幾個程式互相說「我現在要做什麼、用了多少」，誰需要就讓誰先跑。

這個檔是共用的：SortCut 和 FusionStitch 用的是同一份，**整個檔直接複製過去就能用**（只用 Python 標準函式庫、沒有 Qt）。
完整規則看 HANDSHAKE_握手協定.md。

用法（跟第 1 版一樣，舊的呼叫都不用改）：
    import app_handshake as HS
    hs = HS.Handshake('FusionStitch', 'FusionStitch', '2.20.0', port=8361, state=my_state)   # my_state() 回傳下面 STATE 的格式
    hs.start()
    d = hs.decide()        # {'mode': 'alone'|'lead'|'yield'|'split', 'peer', 'why', 'cpu_cap', 'nvenc_cap', 'no_new_heavy', ...}
    hs.stop()              # 關程式時（會刪掉自己的檔，對方就知道你不在了）；熱更新時 hs.stop(remove=False)

怎麼交換：每個程式每 2 秒把自己的狀態寫到 %LOCALAPPDATA%\\AppHandshake\\<app>.json（先寫暫存檔再改名，不會讀到一半），
並讀別人的檔。超過 10 秒沒更新、或 pid 已經不在 → 當作不在。不用網路、不用開埠、熱更新也不會斷。

規則第 2 版新增：
  * 🆙 誰新就用誰的規則：每個程式把自己這份檔放到 AppHandshake\\lib\\app_handshake_r<RULES>.py。
    兩邊都用資料夾裡**最新的規則版本**來算 decide()，所以只要更新其中一邊，另一邊下一輪（2 秒內）就照新規則做，
    不用兩邊都更新。改規則的人：把 RULES 加 1（同一個 RULES 不要放兩種內容）。
  * ⚖ 分配拉桿（兩邊共用）：AppHandshake\\policy.json 的 share（例如 FusionStitch 40、SortCut 60），
    兩邊都在做重工作時照這個比例分；任何一邊改，兩邊一起變（最後改的為準）。
  * 🧑‍💻 留給使用者：有人在用這台電腦（鍵盤／滑鼠 idle_min 分鐘內動過）時，兩邊合起來只用 100 − reserve %
    （預設留 40%：平分時 50/50 → 30/30）。

規則第 3 版（三個程式：FusionStitch、SortCut、ComfyRemote；2026-11-21 使用者定的）：
  * 一直留 reserve %（預設 10%）給使用者：大家合起來最多用 90%。
  * FusionStitch 和 SortCut 都在忙 → 照 share 分（預設各 45%）；只有一個在忙 → 它用 90%。
  * 👑 boss（預設 ComfyRemote）一說要效能（want ≥ 2）→ 它 boss_cap（80%），其他人都降到 others_cap（5%）。
  * ✅ members：這台電腦有誰參與（使用者勾）；沒勾的程式就算有寫狀態檔也不算。None ＝ 大家都算。
  * 回傳多了 level（1～10，給右上角 Lv 用：cap 的十分之一，不限＝10）。
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

PROTO = 'apphs/1'
RULES = 3              # 規則版本：改 decide 的規則就加 1（越大越新）
EVERY = 2.0            # 幾秒寫一次
STALE = 10.0           # 幾秒沒更新算不在
LOSE_CPU = 20.0        # 讓路的那邊：重工作最多用整台 CPU 的 %
SPLIT_CPU = 45.0       # （第 1 版用的平分上限；第 2 版改成看 policy）
WANT_TXT = {0: '閒著', 1: '背景小工作', 2: '在做重工作', 3: '使用者在等'}
APPS = ['FusionStitch', 'SortCut', 'ComfyRemote', 'TikLive']      # 勾選「這台電腦有誰參與」用的清單
POLICY_RV = 3          # policy.json 的版本：舊版存的 reserve／idle 這類預設值換成第 3 版的
POLICY_DEFAULT = {'share': {}, 'reserve': 10, 'idle_min': 3, 'reserve_on': True, 'reserve_when': 'always',
                  'members': None, 'boss': ['ComfyRemote'], 'boss_cap': 80, 'others_cap': 5}
_PKEYS = ('share', 'reserve', 'idle_min', 'reserve_on', 'reserve_when', 'members', 'boss', 'boss_cap', 'others_cap')


def folder():
    base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    p = Path(base) / 'AppHandshake'
    try: p.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    return p


def pid_alive(pid):
    try: pid = int(pid)
    except (TypeError, ValueError): return False
    if pid <= 0: return False
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes as W
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; k.OpenProcess.restype = W.HANDLE
            k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]; k.CloseHandle.argtypes = [W.HANDLE]
            h = k.OpenProcess(0x1000, False, pid)                                 # PROCESS_QUERY_LIMITED_INFORMATION
            if not h: return False
            try:
                c = W.DWORD(); ok = k.GetExitCodeProcess(h, ctypes.byref(c)); return bool(ok) and c.value == 259   # STILL_ACTIVE
            finally: k.CloseHandle(h)
        except Exception: return True
    try: os.kill(pid, 0); return True
    except OSError: return False


def idle_seconds():
    """這台電腦的鍵盤／滑鼠多久沒動（秒）；不是 Windows → None。兩邊讀到的是同一個值."""
    if os.name != 'nt': return None
    try:
        import ctypes

        class LII(ctypes.Structure):
            _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
        li = LII(); li.cbSize = ctypes.sizeof(LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)): return None
        return max(0.0, ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception: return None


# ---------------------------------------------------------------- ⚖ 共用設定（拉桿）
def policy():
    """AppHandshake\\policy.json：{'share': {app: %}, 'reserve': %, 'idle_min': 分, 'reserve_on': bool, 't', 'by'}."""
    P = dict(POLICY_DEFAULT); P['share'] = {}
    try:
        d = json.loads((folder() / 'policy.json').read_text(encoding='utf-8'))
        if isinstance(d, dict):
            keep = _PKEYS + ('t', 'by', 'rv') if int(d.get('rv') or 0) >= POLICY_RV else ('share', 't', 'by')   # 舊版的預設值不要
            P.update({k: v for k, v in d.items() if k in keep})
    except Exception: pass
    try: P['share'] = {str(k): max(5, min(95, float(v))) for k, v in dict(P.get('share') or {}).items()}
    except Exception: P['share'] = {}
    try: P['reserve'] = max(0, min(80, float(P.get('reserve') if P.get('reserve') is not None else 40)))
    except Exception: P['reserve'] = 40
    try: P['idle_min'] = max(0.5, min(60, float(P.get('idle_min') or 3)))
    except Exception: P['idle_min'] = 3
    P['reserve_on'] = P.get('reserve_on') is not False
    P['reserve_when'] = 'active' if P.get('reserve_when') == 'active' else 'always'
    m = P.get('members'); P['members'] = [str(x) for x in m] if isinstance(m, list) else None
    b = P.get('boss'); P['boss'] = [str(x) for x in b] if isinstance(b, list) else ['ComfyRemote']
    try: P['boss_cap'] = max(10, min(95, float(P.get('boss_cap') or 80)))
    except Exception: P['boss_cap'] = 80
    try: P['others_cap'] = max(1, min(50, float(P.get('others_cap') if P.get('others_cap') is not None else 5)))
    except Exception: P['others_cap'] = 5
    return P


def set_policy(by='', **kw):
    """改共用設定（大家都會照新的做）。members=['FusionStitch','SortCut'] ＝ 只有這兩個參與；members=None ＝ 大家都算。例：set_policy('FusionStitch', share={'FusionStitch': 40, 'SortCut': 60})、
    set_policy(by, reserve=40)、set_policy(by, reserve_on=False)。回傳新的設定."""
    P = policy()
    for k in _PKEYS:
        if k in kw and (kw[k] is not None or k == 'members'):
            if k == 'share': P['share'] = {**P.get('share', {}), **{str(a): float(v) for a, v in dict(kw[k]).items()}}
            else: P[k] = kw[k]
    P['t'] = time.time(); P['by'] = str(by); P['rv'] = POLICY_RV
    p = folder() / 'policy.json'; tmp = p.with_suffix('.tmp')
    try: tmp.write_text(json.dumps(P, ensure_ascii=False), encoding='utf-8'); os.replace(tmp, p)
    except Exception: pass
    return policy()


def share_of(app, peers, P=None):
    """這邊在「兩邊都在做重工作」時分到整份的幾 %（沒設過 → 大家一樣）."""
    P = P or policy(); S = P.get('share') or {}
    mine = float(S.get(app, 50))
    tot = mine + sum(float(S.get(str(p.get('app')), 50)) for p in peers)
    return 100.0 * mine / tot if tot > 0 else 50.0


def lead_of(a, b):
    """兩邊算出來一定一樣：want 大的先；一樣看 prio（使用者設的，大的先）；再一樣 → 平分（回 None）.
    want 0（閒著）的那邊不用讓、也不用搶."""
    wa, wb = int(a.get('want') or 0), int(b.get('want') or 0)
    if wa != wb: return a if wa > wb else b
    pa, pb = int(a.get('prio') if a.get('prio') is not None else 5), int(b.get('prio') if b.get('prio') is not None else 5)
    if pa != pb: return a if pa > pb else b
    return None


def _brief(p):
    return {'app': p.get('app'), 'name': p.get('name'), 'ver': p.get('ver'), 'want': p.get('want'), 'prio': p.get('prio'), 'what': p.get('what'),
            'use': p.get('use'), 'age': round(time.time() - float(p.get('t') or 0), 1), 'mode': p.get('mode'), 'rules': p.get('rules', 1)}


def member(app, pol):
    m = pol.get('members')
    return m is None or str(app) in m


def decide_with(me, P, pol=None, idle=None):
    """規則本體（第 3 版）。me＝自己的狀態、P＝還在的別人（list）、pol＝policy()、idle＝idle_seconds().
    回傳的欄位跟第 1、2 版一樣（舊程式照用），另外有 share／budget／user_active／level／members."""
    pol = pol or policy()
    active = idle is not None and idle < float(pol.get('idle_min') or 3) * 60
    use_res = pol.get('reserve_on', True) and (pol.get('reserve_when') == 'always' or active)
    budget = 100.0 - float(pol.get('reserve') or 0) if use_res else 100.0
    my = str(me.get('app') or '')
    Q = [p for p in P if member(p.get('app'), pol)]
    heavy = lambda x: int(x.get('want') or 0) >= 2
    nm_ = lambda x: x.get('name') or x.get('app')
    mx_nv = int((me.get('max') or {}).get('nvenc') or 8)
    base = {'cpu_cap': None, 'nvenc_cap': None, 'no_new_heavy': False, 'peers': [_brief(p) for p in P], 'rules': RULES,
            'user_active': bool(active), 'budget': round(budget, 1), 'share': None, 'members': pol.get('members'), 'peer': ''}

    def out(mode, cap, why, **kw):
        d = {**base, 'mode': mode, 'why': why, **kw}
        if cap is not None and cap < 99.9:
            d['cpu_cap'] = round(cap, 1); d['nvenc_cap'] = max(1, int(round(mx_nv * cap / 100.0)))
        d['level'] = 10 if d['cpu_cap'] is None else max(1, min(10, int(d['cpu_cap'] / 10.0 + 0.5)))
        return d
    if not me.get('on', True):
        return out('alone', None, '握手關著')
    if not member(my, pol):
        return out('alone', None, '這台電腦沒勾這個程式參與，照常跑')
    mine = heavy(me)
    bosses = list(pol.get('boss') or [])
    rest = 100.0 - budget
    if my in bosses and mine:                                                     # 👑 這邊最優先
        others = [p for p in Q if heavy(p) and str(p.get('app')) not in bosses]
        b2 = [p for p in Q if heavy(p) and str(p.get('app')) in bosses]
        cap = float(pol['boss_cap']) / (1 + len(b2))
        return out('lead', cap, f'這邊最優先：用 {cap:.0f}%' + (f'，{"、".join(nm_(p) for p in others)} 先降到 {pol["others_cap"]:.0f}%' if others else ''),
                   peer=nm_(others[0]) if others else '')
    bp = [p for p in Q if heavy(p) and str(p.get('app')) in bosses]
    if bp:                                                                        # 👑 別人最優先
        oc = float(pol['others_cap'])
        return out('yield', oc if mine else None, f'{nm_(bp[0])} 最優先（{pol["boss_cap"]:.0f}%），這邊先降到 {oc:.0f}%', peer=nm_(bp[0]),
                   peer_want=int(bp[0].get('want') or 0), peer_what=bp[0].get('what', ''))
    H = [p for p in Q if heavy(p)]
    if not mine or not H:
        why = '沒有別的程式在忙' if mine else '這邊沒在做重工作'
        return out('alone', budget if mine else None, why + (f'（留 {rest:.0f}% 給你 → 最多 {budget:.0f}%）' if mine and rest > 0 else ''))
    up = [p for p in H if lead_of(me, p) is p]
    if up:                                                                        # 有人優先比這邊高
        p = up[0]; w = int(p.get('want') or 0)
        return out('yield', min(LOSE_CPU, budget), f'讓 {nm_(p)} 先跑（它{WANT_TXT.get(w, "")}' + (f'：{p.get("what")}' if p.get('what') else '') + '）',
                   peer=nm_(p), peer_want=w, peer_what=p.get('what', ''), no_new_heavy=w >= 2)
    tie = [p for p in H if lead_of(me, p) is None]
    low = [p for p in H if lead_of(me, p) is me]
    room = budget - LOSE_CPU * len(low)
    if not tie:
        return out('lead', room, f'這邊先跑，{"、".join(nm_(p) for p in low)} 讓路', peer=nm_(low[0]))
    sh = share_of(my, tie, pol); cap = room * sh / 100.0
    return out('split', cap, f'跟 {"、".join(nm_(p) for p in tie)} 分：這邊 {cap:.0f}%' + (f'（留 {rest:.0f}% 給你）' if rest > 0 else ''),
               share=round(sh, 1), peer=nm_(tie[0]), peer_want=int(tie[0].get('want') or 0), peer_what=tie[0].get('what', ''))


# ---------------------------------------------------------------- 🆙 誰新就用誰的規則
def lib_dir():
    p = folder() / 'lib'
    try: p.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    return p


def publish(src=None):
    """把這份檔放到 lib\\app_handshake_r<RULES>.py（已經有同一版就不動）."""
    try:
        src = Path(src or __file__); dst = lib_dir() / f'app_handshake_r{RULES}.py'
        if not dst.exists():
            tmp = dst.with_suffix('.tmp'); tmp.write_bytes(src.read_bytes()); os.replace(tmp, dst)
    except Exception: pass


_LIB = {'n': 0, 'mod': None, 'mt': 0.0, 'err': '', 't': 0.0}


def newest_rules():
    """資料夾裡最新、而且比這份新的規則（module）；沒有就 None。每 10 秒看一次."""
    now = time.time()
    if now - _LIB['t'] < 10: return _LIB['mod'] if _LIB['n'] > RULES else None
    _LIB['t'] = now
    best = (RULES, None)
    try:
        for f in lib_dir().glob('app_handshake_r*.py'):
            try: n = int(f.stem.rsplit('_r', 1)[1])
            except Exception: continue
            if n > best[0]: best = (n, f)
    except Exception: pass
    n, f = best
    if f is None: _LIB.update(n=0, mod=None); return None
    try:
        mt = f.stat().st_mtime
        if _LIB['n'] == n and _LIB['mt'] == mt and _LIB['mod'] is not None: return _LIB['mod']
        import importlib.util
        spec = importlib.util.spec_from_file_location(f'app_handshake_r{n}', str(f)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        if int(getattr(m, 'RULES', 0)) != n or not callable(getattr(m, 'decide_with', None)): raise ValueError('不是握手規則檔')
        _LIB.update(n=n, mod=m, mt=mt, err='')
        return m
    except Exception as ex:
        _LIB.update(n=0, mod=None, err=f'第 {n} 版規則載入失敗：{ex}'[:160])
        return None


class Handshake:
    def __init__(self, app, name='', ver='', port=0, state=None):
        self.app = str(app); self.name = name or self.app; self.ver = str(ver); self.port = int(port or 0)
        self.state = state or (lambda: {}); self.stop_ = False; self.me = {}; self.others = {}; self.err = ''; self.t = 0.0
        self.path = folder() / f'{self.app}.json'; self.th = None

    # ---------------------------------------------------------------- 寫自己、讀別人
    def _me(self):
        try: S = dict(self.state() or {})
        except Exception as ex: S = {}; self.err = f'state：{ex}'[:160]
        m = newest_rules()
        return {'proto': PROTO, 'app': self.app, 'name': self.name, 'ver': self.ver, 'pid': os.getpid(), 'port': self.port,
                'host': os.environ.get('COMPUTERNAME', ''), 't': time.time(), 'on': S.get('on', True) is not False,
                'want': max(0, min(3, int(S.get('want') or 0))), 'prio': max(0, min(10, int(S.get('prio') if S.get('prio') is not None else 5))),
                'what': str(S.get('what') or '')[:120], 'use': dict(S.get('use') or {}), 'max': dict(S.get('max') or {}),
                'rules': int(getattr(m, 'RULES', RULES)) if m else RULES, 'rules_own': RULES,
                'mode': (self.decide().get('mode') if self.me else 'alone')}

    def write(self):
        self.me = self._me(); tmp = self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(self.me, ensure_ascii=False), encoding='utf-8'); os.replace(tmp, self.path); self.err = ''
        except Exception as ex: self.err = f'寫不進去：{ex}'[:160]

    def read(self):
        O = {}; now = time.time()
        try:
            for p in folder().glob('*.json'):
                if p.stem.lower() in (self.app.lower(), 'policy'): continue
                try: d = json.loads(p.read_text(encoding='utf-8'))
                except Exception: continue
                if not str(d.get('proto', '')).startswith('apphs/'): continue
                if now - float(d.get('t') or 0) > STALE or not pid_alive(d.get('pid')): continue
                O[str(d.get('app') or p.stem)] = d
        except Exception as ex: self.err = f'讀不到：{ex}'[:160]
        self.others = O; self.t = now
        return O

    def peers(self):
        now = time.time()
        return [d for d in self.others.values() if now - float(d.get('t') or 0) <= STALE and d.get('on', True) is not False]

    # ---------------------------------------------------------------- 誰先
    def decide(self):
        """回自己現在要怎麼做。用資料夾裡最新的規則算（兩邊規則一樣、資料一樣 → 結果對得起來）."""
        me = self.me or {}; P = self.peers(); pol = policy(); idle = idle_seconds()
        m = newest_rules()
        if m is not None:
            try:
                d = dict(m.decide_with(me, P, pol, idle)); d.setdefault('rules', int(getattr(m, 'RULES', 0))); d['rules_from'] = 'lib'
                return d
            except Exception as ex: self.err = f'新規則出錯，先用自己的：{ex}'[:160]
        d = decide_with(me, P, pol, idle); d['rules_from'] = 'own'
        return d

    _brief = staticmethod(_brief)

    # ---------------------------------------------------------------- 背景
    def _loop(self):
        publish()
        while not self.stop_:
            try: self.read(); self.write()
            except Exception as ex: self.err = str(ex)[:160]
            time.sleep(EVERY)

    def start(self):
        if self.th is not None and self.th.is_alive(): return self
        self.stop_ = False; self.th = threading.Thread(target=self._loop, name='AppHandshake', daemon=True); self.th.start()
        return self

    def stop(self, remove=True):
        self.stop_ = True
        if remove:
            try: self.path.unlink()
            except Exception: pass
