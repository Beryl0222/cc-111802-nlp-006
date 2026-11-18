import hashlib
import re
import unicodedata


TOKEN_RE = re.compile(r"[㐀-鿿]|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:\.\d+)?|[^\w\s]", re.UNICODE)
SENTENCE_RE = re.compile(r"[^。！？；!?;\n]+(?:[。！？；!?;]+|$)")
CLAUSE_SPLIT_RE = re.compile(r"[，,、]")

# NFKC 会把中文全角标点折叠为半角；归一化后按字符位置还原这些中文标点。
_CJK_PUNCTUATION = {"，": ",", "：": ":", "；": ";", "（": "(", "）": ")", "！": "!", "？": "?"}
_HALFWIDTH_TO_CJK = {value: key for key, value in _CJK_PUNCTUATION.items()}

# 低年级可替换的书面连接词，仅在受保护术语之外的文本上生效
CONNECTIVE_REPLACEMENTS = {
    "然而": "可是",
    "因此": "所以",
    "由于": "因为",
    "此外": "另外",
    "倘若": "如果",
    "并且": "而且",
}


def normalize_text(text):
    """空白折叠 + 兼容性归一（全角拉丁字母/数字转半角），同时保留中文全角标点。

    NFKC 对上述字符是一对一兼容映射、不改变长度，因此可按字符位置把
    被折叠的中文标点（，：；（）等）还原。
    """
    source = str(text)
    folded = unicodedata.normalize("NFKC", source)
    if len(folded) == len(source):
        folded = "".join(
            _HALFWIDTH_TO_CJK.get(folded[index], folded[index])
            if source[index] in _CJK_PUNCTUATION else folded[index]
            for index in range(len(folded))
        )
    return " ".join(folded.split())


def tokenize(text):
    return TOKEN_RE.findall(normalize_text(text))


def annotate(record):
    tokens = tokenize(record["text"])
    unique = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return {
        "tokens": tokens,
        "terms": unique,
        "character_count": len(normalize_text(record["text"])),
        "token_count": len(tokens),
    }


def content_hash(text):
    """句子内容指纹，用于判断原文事实是否发生变化。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:12]


def split_sentences(text):
    """把原文切分为带顺序的句子，切分结果用于分配稳定句子 ID。"""
    normalized = normalize_text(text)
    sentences = []
    for match in SENTENCE_RE.findall(normalized):
        sentence = match.strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def find_term_spans(text, terms):
    """在文本中定位术语（含别名），长词优先且不允许重叠，返回带位置的命中列表。"""
    surfaces = []
    for term in terms:
        for surface in (term.canonical, *term.aliases):
            surfaces.append((surface, term))
    surfaces.sort(key=lambda item: len(item[0]), reverse=True)

    spans = []
    occupied = []
    for surface, term in surfaces:
        start = 0
        while True:
            index = text.find(surface, start)
            if index < 0:
                break
            end = index + len(surface)
            if all(end <= occupied_start or index >= occupied_end for occupied_start, occupied_end in occupied):
                spans.append({
                    "start": index,
                    "end": end,
                    "surface": surface,
                    "term_id": term.term_id,
                    "canonical": term.canonical,
                    "category": term.category,
                })
                occupied.append((index, end))
            start = index + 1
    spans.sort(key=lambda span: span["start"])
    return spans


def _split_clauses(full_text, threshold):
    if len(full_text) <= threshold or not CLAUSE_SPLIT_RE.search(full_text):
        return [full_text]
    clauses = [clause.strip("，,、 ") for clause in CLAUSE_SPLIT_RE.split(full_text) if clause.strip("，,、 ")]
    result = []
    for clause in clauses:
        if len(clause) > threshold and "、" in clause:
            result.extend(part.strip() for part in re.split("、", clause) if part.strip())
        else:
            result.append(clause)
    return result


def simplify_sentence(source_text, terms, profile):
    """确定性句式简化：替换连接词、按年级阈值拆短句；术语片段受保护，逐字保留。"""
    spans = find_term_spans(source_text, terms)
    runs = []
    cursor = 0
    for span in spans:
        if span["start"] > cursor:
            runs.append([source_text[cursor:span["start"]], False])
        runs.append([source_text[span["start"]:span["end"]], True])
        cursor = span["end"]
    if cursor < len(source_text):
        runs.append([source_text[cursor:], False])

    if profile.get("replace_connectives"):
        for run in runs:
            if not run[1]:
                for old, new in CONNECTIVE_REPLACEMENTS.items():
                    run[0] = run[0].replace(old, new)

    full_text = "".join(run[0] for run in runs)
    clauses = _split_clauses(full_text, profile["split_threshold"])
    finished = []
    for clause in clauses:
        clause = clause.strip()
        if clause and clause[-1] not in "。！？":
            clause += "。"
        finished.append(clause)
    return "".join(finished)


def analyze_mentions(source_text, adapted_text, terms, glossary_version):
    """比对原句与改写句中的术语，标注来源并给出术语保全告警。"""
    source_spans = find_term_spans(source_text, terms)
    adapted_spans = find_term_spans(adapted_text, terms)
    source_ids = {span["term_id"] for span in source_spans}
    source_canonical = {span["term_id"]: span["canonical"] for span in source_spans}

    mentions = []
    adapted_ids = set()
    for span in adapted_spans:
        adapted_ids.add(span["term_id"])
        mentions.append({
            "term_id": span["term_id"],
            "surface": span["surface"],
            "canonical": span["canonical"],
            "category": span["category"],
            "provenance": "source" if span["term_id"] in source_ids else "teacher",
            "glossary_version": glossary_version,
        })

    warnings = []
    for term_id in source_ids - adapted_ids:
        warnings.append(f"原文术语缺失：{source_canonical[term_id]}")
    for span in adapted_spans:
        if span["term_id"] not in source_ids:
            warnings.append(f"教师新增术语需核对：{span['canonical']}")
    return mentions, _dedupe(warnings)


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
