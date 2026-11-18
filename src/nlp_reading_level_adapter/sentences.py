from .text import content_hash, split_sentences


def _sequence(sentence_id):
    digits = "".join(char for char in sentence_id if char.isdigit())
    return int(digits) if digits else 0


def build_sentences(text):
    """首次入库：按顺序分配稳定句子 ID 并记录内容指纹。"""
    return [
        {
            "sentence_id": f"s{index}",
            "index": index,
            "text": part,
            "hash": content_hash(part),
        }
        for index, part in enumerate(split_sentences(text), start=1)
    ]


def align_sentences(previous, current_text):
    """修订入库：对齐新旧句子，尽量保留稳定句子 ID。

    判定顺序：
    1. 同位置且指纹一致 → unchanged（直接锁定，避免把“删除后前移”误判为移动）
    2. 剩余旧句与剩余新句按指纹匹配 → moved（位置移动，事实未变，改写仍有效）
    3. 仍未配对的旧句与新句按序配对 → changed（沿用旧 ID，旧片段据此暴露过期）
    4. 多出的新句记为 added（分配新 ID），多出的旧句记为 removed
    """
    parts = split_sentences(current_text)
    previous = list(previous)
    next_seq = max((_sequence(item["sentence_id"]) for item in previous), default=0) + 1

    changes = []
    sentences = []
    used_old = set()
    used_new = set()

    # 1. 同位哈希配对：unchanged
    for index, part in enumerate(parts, start=1):
        if index > len(previous):
            break
        old_item = previous[index - 1]
        if old_item["hash"] == content_hash(part):
            used_old.add(old_item["sentence_id"])
            used_new.add(index)
            sentences.append({
                "sentence_id": old_item["sentence_id"],
                "index": index,
                "text": part,
                "hash": old_item["hash"],
            })

    remaining_old = [item for item in previous if item["sentence_id"] not in used_old]
    remaining_new = [(index, part) for index, part in enumerate(parts, start=1)
                     if index not in used_new]

    # 2. 跨位哈希配对：moved
    still_remaining_new = []
    for index, part in remaining_new:
        new_hash = content_hash(part)
        match = next((item for item in remaining_old
                      if item["sentence_id"] not in used_old and item["hash"] == new_hash), None)
        if match is None:
            still_remaining_new.append((index, part))
            continue
        used_old.add(match["sentence_id"])
        used_new.add(index)
        changes.append({
            "sentence_id": match["sentence_id"],
            "kind": "moved",
            "old_index": match["index"],
            "new_index": index,
            "old_text": match["text"],
            "new_text": part,
        })
        sentences.append({
            "sentence_id": match["sentence_id"],
            "index": index,
            "text": part,
            "hash": new_hash,
        })

    remaining_old = [item for item in previous if item["sentence_id"] not in used_old]
    remaining_new = still_remaining_new

    # 3. 按序配对：changed
    pair_count = min(len(remaining_old), len(remaining_new))
    for old_item, (new_index, new_text) in zip(remaining_old[:pair_count], remaining_new[:pair_count]):
        used_old.add(old_item["sentence_id"])
        changes.append({
            "sentence_id": old_item["sentence_id"],
            "kind": "changed",
            "old_index": old_item["index"],
            "new_index": new_index,
            "old_text": old_item["text"],
            "new_text": new_text,
        })
        sentences.append({
            "sentence_id": old_item["sentence_id"],
            "index": new_index,
            "text": new_text,
            "hash": content_hash(new_text),
        })

    # 4. added / removed
    for new_index, new_text in remaining_new[pair_count:]:
        sentence_id = f"s{next_seq}"
        next_seq += 1
        changes.append({
            "sentence_id": sentence_id,
            "kind": "added",
            "old_index": None,
            "new_index": new_index,
            "old_text": None,
            "new_text": new_text,
        })
        sentences.append({
            "sentence_id": sentence_id,
            "index": new_index,
            "text": new_text,
            "hash": content_hash(new_text),
        })

    for old_item in remaining_old[pair_count:]:
        changes.append({
            "sentence_id": old_item["sentence_id"],
            "kind": "removed",
            "old_index": old_item["index"],
            "new_index": None,
            "old_text": old_item["text"],
            "new_text": None,
        })

    sentences.sort(key=lambda item: item["index"])
    return sentences, changes
