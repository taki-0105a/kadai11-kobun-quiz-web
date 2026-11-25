"""
古文単語・文法テスト（Web版）
--------------------------------
自作の iOS アプリ「KobunQuiz」を Streamlit に移植した簡易版。
生徒が iPhone を持っていなくても、URL ひとつで同じ問題を解けるようにすることが目的。

【iOS版から引き継いだ設計】
  ・選択肢は「同じ見た目の対象語が取りうる解答」からのみ作る
    （「き」の問題に「けり」を混ぜると、本文を読まずに消去できてしまうため）
  ・requiredDistractors（教育的に紛らわしい誤答）は必ず選択肢に入れる
  ・選択肢が2つ未満しか作れない問題は「識別が成立しない」ものとして出題しない

【Web版で割り切った点】
  ・成績の保存はブラウザを閉じるまで（サーバに個人データを残さない設計）
  ・学習カード／誤答報告機能は省略し、出題と復習に絞る
"""

import json
import random
from pathlib import Path

import streamlit as st

DATA_PATH = Path(__file__).parent / "questions.json"

HONORIFIC_TYPES = ["尊敬語", "謙譲語", "丁寧語"]
MAJOR_ORDER = ["助動詞", "助詞", "敬語", "文学史"]
DIFFICULTIES = ["基礎", "標準", "発展"]

INK = "#333333"        # 本文
ACCENT = "#B91C1C"     # 朱色（対象語）
NAVY = "#1F3A5F"       # 基調色

WORK_ALIASES = {
    "古今集": "古今和歌集",
    "新古今集": "新古今和歌集",
    "拾遺集": "拾遺和歌集",
    "後撰集": "後撰和歌集",
    "後拾遺集": "後拾遺和歌集",
    "百人一首25番": "小倉百人一首",
    "百人一首84番": "小倉百人一首",
}


# ============================================================ 問題データの整形
def work_of(q):
    """出典から作品名だけを切り出す（「源氏物語（桐壺）」→「源氏物語」）。"""
    name = q.get("source", "") or ""
    for sep in ["・", "（", "／", " ", "　"]:
        if sep in name:
            name = name.split(sep)[0]
    name = name.strip()
    return WORK_ALIASES.get(name, name)


def display_target(q):
    """表示用の対象語。「の（桜の）」のような注記は落とす。"""
    t = q.get("target", "") or ""
    return t.split("（")[0] if "（" in t else t


def honorific_type(q):
    for h in HONORIFIC_TYPES:
        if (q.get("grammar") or "").startswith(h):
            return h
    return None


def style_of(q):
    """何を問う問題か。iOS版の Question.style と同じ判定。"""
    if q.get("historyPrompt") and q.get("historyAnswer"):
        return "literaryHistory"
    if q.get("meaning"):
        return "meaning"
    if q.get("majorCategory") == "敬語" and honorific_type(q):
        return "honorificType"
    return "identification"


def answer_of(q):
    s = style_of(q)
    if s == "identification":
        return q.get("grammar")
    if s == "honorificType":
        return honorific_type(q) or q.get("grammar")
    if s == "meaning":
        return q.get("meaning") or q.get("grammar")
    return q.get("historyAnswer") or q.get("grammar")


def prompt_of(q):
    # 設問文を問題ごとに持つ問題（敬意の方向・動作主など）はそれを優先する（iOS版と同じ）
    if q.get("promptText"):
        return q["promptText"]
    s = style_of(q)
    t = display_target(q)
    if s == "identification":
        return f"朱色の「{t}」の文法的説明として正しいものを選べ。"
    if s == "honorificType":
        return f"朱色の「{t}」は敬語のどれにあたるか。"
    if s == "meaning":
        return f"朱色の「{t}」の意味・用法として正しいものを選べ。"
    return q.get("historyPrompt") or ""


@st.cache_data(show_spinner="問題を読み込み中…")
def load_bank():
    """問題を読み込み、選択肢プールと出題可能IDを一度だけ作る。

    毎回数え直すと全問走査が入れ子になるので、iOS版と同じく起動時に索引を作る。
    """
    questions = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    answer_pools = {}    # (小分類, 見た目) -> その形が取りうる解答の集合
    meaning_pools = {}   # 小分類 -> 意味ラベルの集合
    for q in questions:
        s = style_of(q)
        if s == "identification":
            key = (q["subCategory"], display_target(q))
            answer_pools.setdefault(key, set()).add(answer_of(q))
        elif s == "meaning":
            meaning_pools.setdefault(q["subCategory"], set()).add(q["meaning"])

    quizzable = set()
    for q in questions:
        s = style_of(q)
        req = q.get("requiredDistractors") or []
        if s == "honorificType":
            ok = True
        elif s == "literaryHistory":
            ok = len(req) >= 3
        elif s == "meaning":
            ok = len(meaning_pools.get(q["subCategory"], ())) >= 2 or bool(req)
        else:
            pool = answer_pools.get((q["subCategory"], display_target(q)), set())
            ok = len(pool) >= 2 or bool(req)
        if ok:
            quizzable.add(q["id"])

    for q in questions:
        q["_work"] = work_of(q)
        q["_answer"] = answer_of(q)
        q["_prompt"] = prompt_of(q)
        q["_style"] = style_of(q)

    # 選択肢プールは JSON 化できるよう list に直しておく
    answer_pools = {k: sorted(v) for k, v in answer_pools.items()}
    meaning_pools = {k: sorted(v) for k, v in meaning_pools.items()}
    return questions, answer_pools, meaning_pools, quizzable


QUESTIONS, ANSWER_POOLS, MEANING_POOLS, QUIZZABLE = load_bank()
BY_ID = {q["id"]: q for q in QUESTIONS}


def make_choices(q, count=4, seed=None):
    """正解 → 確定誤答 → プールから補充、の順に選択肢を組む（iOS版と同じ手順）。"""
    s = q["_style"]
    if s == "honorificType":
        pool = list(HONORIFIC_TYPES)
    elif s == "meaning":
        pool = list(MEANING_POOLS.get(q["subCategory"], []))
    elif s == "identification":
        pool = list(ANSWER_POOLS.get((q["subCategory"], display_target(q)), []))
    else:
        pool = []          # 文学史は誤答を問題ごとに持っている

    rng = random.Random(seed)
    chosen = [q["_answer"]]
    for d in q.get("requiredDistractors") or []:
        if d != q["_answer"] and d not in chosen and len(chosen) < count:
            chosen.append(d)
    filler = [p for p in pool if p not in chosen]
    rng.shuffle(filler)
    for d in filler:
        if len(chosen) >= count:
            break
        chosen.append(d)
    rng.shuffle(chosen)
    return chosen


def highlighted(q):
    """本文の対象箇所を朱色にした HTML を返す。位置不明ならそのまま。"""
    text = q.get("text") or ""
    start, length = q.get("targetStart"), q.get("targetLength")
    if start is None or length is None or start < 0 or length <= 0 or start + length > len(text):
        return text
    return (
        text[:start]
        + f'<span style="color:{ACCENT};font-weight:700;">{text[start:start + length]}</span>'
        + text[start + length:]
    )


# ================================================================ 画面の状態
st.set_page_config(page_title="古文文法テスト", page_icon="📜", layout="centered")

ss = st.session_state
ss.setdefault("stage", "select")   # select / quiz / result
ss.setdefault("queue", [])         # 出題する問題ID
ss.setdefault("idx", 0)
ss.setdefault("choices", [])
ss.setdefault("selected", None)
ss.setdefault("results", [])       # (id, 正誤, 選んだ選択肢)
ss.setdefault("title", "")


def start_quiz(ids, title, limit=None):
    ids = list(ids)
    random.shuffle(ids)
    if limit:
        ids = ids[:limit]
    ss.stage = "quiz"
    ss.queue = ids
    ss.idx = 0
    ss.results = []
    ss.selected = None
    ss.title = title
    ss.choices = make_choices(BY_ID[ids[0]], seed=ids[0])


def go_select():
    ss.stage = "select"
    ss.queue, ss.results, ss.idx, ss.selected = [], [], 0, None


# ================================================================== 出題設定
st.markdown(
    f"<h2 style='color:{NAVY};margin-bottom:0;'>📜 古文文法テスト</h2>"
    f"<p style='color:#667;margin-top:4px;'>助動詞・助詞・敬語の識別／文学史（全 {len(QUIZZABLE)} 問）</p>",
    unsafe_allow_html=True,
)

if ss.stage == "select":
    pool = [q for q in QUESTIONS if q["id"] in QUIZZABLE]

    mode = st.radio(
        "出題範囲",
        ["分野・小分類から", "作品から", "全分野ランダム"],
        horizontal=True,
    )

    target, title = [], ""
    if mode == "分野・小分類から":
        majors = [m for m in MAJOR_ORDER if any(q["majorCategory"] == m for q in pool)]
        major = st.selectbox("分野", majors)
        subs = sorted({q["subCategory"] for q in pool if q["majorCategory"] == major})
        sub = st.selectbox("小分類", ["（この分野すべて）"] + subs)
        if sub == "（この分野すべて）":
            target = [q for q in pool if q["majorCategory"] == major]
            title = f"{major}（全体）"
        else:
            target = [q for q in pool if q["subCategory"] == sub]
            title = sub
    elif mode == "作品から":
        counts = {}
        for q in pool:
            counts[q["_work"]] = counts.get(q["_work"], 0) + 1
        works = sorted((w for w, c in counts.items() if w and c >= 3),
                       key=lambda w: -counts[w])
        work = st.selectbox("作品", works, format_func=lambda w: f"{w}（{counts[w]}問）")
        target = [q for q in pool if q["_work"] == work]
        title = work
    else:
        target = pool
        title = "全分野ランダム"

    levels = st.multiselect("難易度", DIFFICULTIES, default=DIFFICULTIES)
    target = [q for q in target if q["difficulty"] in levels]

    if not target:
        st.warning("条件に合う問題がありません。難易度の選択を広げてください。")
        st.stop()

    n = st.slider("出題数", 5, min(30, len(target)), min(10, len(target)))
    st.caption(f"この条件で出題できるのは {len(target)} 問です。")

    if st.button("開始する", type="primary", width="stretch"):
        start_quiz([q["id"] for q in target], title, limit=n)
        st.rerun()

# ==================================================================== 出題中
elif ss.stage == "quiz":
    q = BY_ID[ss.queue[ss.idx]]
    total = len(ss.queue)

    st.progress(ss.idx / total, text=f"{ss.title}　{ss.idx + 1} / {total} 問")

    # 本文（文学史で本文が無い問題は出さない）
    if q.get("text"):
        st.markdown(
            f"<div style='border-left:4px solid {NAVY};padding:12px 16px;margin:8px 0;"
            f"background:#F7F9FB;font-family:\"Hiragino Mincho ProN\",serif;font-size:20px;"
            f"line-height:1.9;color:{INK};'>{highlighted(q)}"
            + (f"<div style='font-size:14px;color:#667;margin-top:8px;'>（{q['source']}）</div>"
               if q.get("source") else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown(f"**{q['_prompt']}**")

    if ss.selected is None:
        for i, c in enumerate(ss.choices):
            if st.button(f"{'①②③④'[i]} {c}", key=f"c{ss.idx}_{i}", width="stretch"):
                ss.selected = c
                ss.results.append((q["id"], c == q["_answer"], c))
                st.rerun()
    else:
        correct = ss.selected == q["_answer"]
        for i, c in enumerate(ss.choices):
            if c == q["_answer"]:
                st.markdown(
                    f"<div style='background:{NAVY};color:#fff;padding:10px 14px;"
                    f"border-radius:4px;margin:4px 0;'>{'①②③④'[i]} {c}　✓ 正解</div>",
                    unsafe_allow_html=True,
                )
            else:
                mark = "　← あなたの解答" if c == ss.selected else ""
                st.markdown(
                    f"<div style='background:#E5E7EB;color:{INK};padding:10px 14px;"
                    f"border-radius:4px;margin:4px 0;'>{'①②③④'[i]} {c}{mark}</div>",
                    unsafe_allow_html=True,
                )

        # 条件式（式）で書くと Streamlit の magic が戻り値の DeltaGenerator を
        # 画面に描画してしまうため、必ず if 文で書く。
        if correct:
            st.success("正解")
        else:
            st.error("不正解")
        st.markdown(
            f"<div style='border-left:3px solid {ACCENT};padding:8px 14px;margin-top:8px;"
            f"color:{INK};line-height:1.8;'>{q['explanation']}</div>",
            unsafe_allow_html=True,
        )
        if q.get("supplement"):
            with st.expander("補足"):
                st.write(q["supplement"])

        label = "結果を見る" if ss.idx + 1 >= total else "次の問題へ"
        if st.button(label, type="primary", width="stretch"):
            ss.idx += 1
            ss.selected = None
            if ss.idx >= total:
                ss.stage = "result"
            else:
                ss.choices = make_choices(BY_ID[ss.queue[ss.idx]], seed=ss.queue[ss.idx])
            st.rerun()

    if st.button("中断してもどる"):
        go_select()
        st.rerun()

# ==================================================================== 結果
else:
    n = len(ss.results)
    ok = sum(1 for _, c, _ in ss.results if c)
    st.markdown(f"### {ss.title}　の結果")

    c1, c2, c3 = st.columns(3)
    c1.metric("正答数", f"{ok} / {n}")
    c2.metric("正答率", f"{round(ok / n * 100) if n else 0} %")
    c3.metric("誤答", f"{n - ok} 問")

    wrong = [(qid, sel) for qid, c, sel in ss.results if not c]
    if wrong:
        st.markdown("#### 間違えた問題")
        for qid, sel in wrong:
            q = BY_ID[qid]
            with st.expander(f"「{display_target(q)}」（{q['subCategory']}）"):
                if q.get("text"):
                    st.markdown(
                        f"<div style='font-family:\"Hiragino Mincho ProN\",serif;font-size:18px;"
                        f"line-height:1.9;'>{highlighted(q)}</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown(f"- あなたの解答：**{sel}**")
                st.markdown(f"- 正解：**{q['_answer']}**")
                st.markdown(q["explanation"])
    else:
        st.balloons()
        st.info("全問正解です。")

    col1, col2 = st.columns(2)
    if wrong and col1.button("間違えた問題だけ解き直す", type="primary", width="stretch"):
        start_quiz([qid for qid, _ in wrong], "苦手問題の復習")
        st.rerun()
    if col2.button("出題設定にもどる", width="stretch"):
        go_select()
        st.rerun()
