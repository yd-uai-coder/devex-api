"""段階7 横断事項と実装計画の意味モデル(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

段階7は、詳細設計書の 07 横断事項(例外と HTTP・認証・トランザクション・ログ)と、実装計画
(マイルストーン・タスク・開発環境・リスク)を1つの model に持つ(07 の元になるデータも
段階7で作る)。

- タスクはマイルストーンの中に入れ子で持つ。タスクがマイルストーンの名前で参照すると、改名で参照が
  切れるため(WBS も本来は階層)。
- タスクは実装手順書の作業単位(単位)になる。処理を持つものは機能ごとの縦割り(`feature`。
  バックエンド・フロントエンド・テストを1単位に)、処理の無い準備・デプロイは`base`にする。
- マイルストーンの番号(`M-01`…)と単位の ID(`M-01-T01`…)は保存しない。並び順から
  `milestone_id`・`task_id`で導く(L-ID と同じ)。表示の ID は並べ替えで変わるので、段階8の
  手順書と変更の影響は、タスクごとに保存する安定キー(`key`。`U-001`…)で単位を指す。
  キーは`assign_unit_keys`が、キーの無いタスクに`next_unit_seq`から振る(要件ID と同じく、
  消した番号は使い直さない)。
- 単位の間の依存(`depends_on`)は単位の ID で書き、前にある単位だけを指せる(後ろや自分を指すと
  検証のエラー。これで循環も起きない)。並べ替えたときの付け替えは画面の操作が行う。
- 処理ID は段階1の機能一覧の ID で書く。マイルストーンの処理は、そのタスクの処理から導く
  (`milestone_functions`)。どの単位にも入らない処理は、計画の漏れとして検証で警告する。
- タスクのファイルは2つの欄に分ける。`modules`は段階4のモジュール一覧のパスで、検証する。
  `config_files`は`Dockerfile`・`docker-compose.yml`のような環境・設定のファイルの例で、検証しない。
- 横断事項の`modules`は「関わるファイルの例」で、検証しない。
- 横断事項の`req_ids`は、その方針が担う要件ID(非機能要件・制約を含む)。変更の影響を非機能要件
  からたどるために持つ(処理を持たない横断事項へは、段階1の処理からは届かないため)。
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from app.detailed_design.procedure import resolve_callee

# 横断事項と実装計画の段階の番号
PLAN_STAGE = 7

# 再生成でキーを引き継ぐ、タスク名の似ている度合い(difflib の比。言い換え「初期化 → 初期構成」を
# 拾い、別のタスクの名前は拾わない程度)
_TITLE_SIMILARITY = 0.6

Priority = Literal["Must", "Should", "Could"]
# 単位の種別。feature = 処理を持つ縦割りの単位(機能)、base = 処理の無い準備・デプロイ(基盤)
UnitKind = Literal["feature", "base"]

PRIORITIES: tuple[Priority, ...] = ("Must", "Should", "Could")
UNIT_KINDS: tuple[UnitKind, ...] = ("feature", "base")

# 1つの単位に入れる処理の数の目安。超えると検証で警告する(原則は1処理)
MAX_UNIT_FUNCTIONS = 3

# 07 横断事項に必ず書く項目(docs/internal_design.md 3.3節の章構成)。欠けていれば検証で警告する
CROSSCUTTING_TOPICS: tuple[str, ...] = ("例外と HTTP", "認証", "トランザクション", "ログ")


class CrossCuttingRow(BaseModel):
    """07 横断事項の1行。`modules`はその方針に関わるファイルの例(検証しない)、`req_ids`はその方針が
    担う要件ID(要件定義書に無い ID は検証で警告する)。"""

    topic: str
    policy: str = ""
    modules: list[str] = Field(default_factory=list)
    req_ids: list[str] = Field(default_factory=list)


class PlanTask(BaseModel):
    """マイルストーンの中のタスク1つ(実装手順書の作業単位)。

    `depends_on`は先に終わっている必要がある単位の ID、`modules`は段階4のパス(検証する)、
    `config_files`は環境・設定のファイルの例(検証しない)。"""

    # 並べ替えで変わらない単位のキー(`U-001`…)。空なら保存時・生成時に振る
    key: str = ""
    kind: UnitKind = "feature"
    title: str = ""
    function_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    # 要件ID。簡易モードの WBS の「要件」欄だけが埋める(詳細設計モードは段階1の処理から導く)
    req_ids: list[str] = Field(default_factory=list)


class Milestone(BaseModel):
    """マイルストーン1つ。動くようにする処理は、タスクの処理から導く(`milestone_functions`)。"""

    name: str
    goal: str = ""
    priority: Priority = "Must"
    tasks: list[PlanTask] = Field(default_factory=list)


class Risk(BaseModel):
    """想定リスクと対策の1行。"""

    risk: str
    mitigation: str = ""


class PlanModel(BaseModel):
    """段階7の意味モデル。"""

    crosscutting: list[CrossCuttingRow] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    environment: str = ""
    risks: list[Risk] = Field(default_factory=list)
    # 次に振る単位のキーの番号
    next_unit_seq: int = 1


def milestone_id(index: int) -> str:
    """並び順(0始まり)からマイルストーンの番号を導く(`M-01`…)。"""
    return f"M-{index + 1:02d}"


def task_id(milestone_index: int, task_index: int) -> str:
    """マイルストーンとタスクの並び順(0始まり)から単位の ID を導く(`M-01-T01`…)。"""
    return f"{milestone_id(milestone_index)}-T{task_index + 1:02d}"


def renamed_task_ids(written: Sequence[Sequence[str]]) -> dict[str, str]:
    """マイルストーンごとに並べたタスクの書かれた ID(`M-01-T01`)→ 並び順から導いた ID。
    同じ ID が2つ書かれていれば、最初のタスクを指す。依存先を導いた ID に読み替えるのに使う
    (書き手は並べ替えや書き損じで、並び順と違う ID を書くことがある)。"""
    renames: dict[str, str] = {}
    for m, task_ids in enumerate(written):
        for t, written_id in enumerate(task_ids):
            renames.setdefault(written_id.strip().upper(), task_id(m, t))
    return renames


def unit_key(seq: int) -> str:
    """単位のキーを番号から作る(`U-001`…)。"""
    return f"U-{seq:03d}"


def assign_unit_keys(model: PlanModel) -> PlanModel:
    """キーの無いタスク・前のタスクと同じキーのタスクに、次の番号のキーを振る(並び順に)。

    振った分だけ`next_unit_seq`を進める。今あるキーより小さい番号は振らない(`next_unit_seq`が
    古くても、既存のキーと重ならないようにする)。"""
    tasks = [task for milestone in model.milestones for task in milestone.tasks]
    used = {task.key for task in tasks if task.key}
    seq = max([model.next_unit_seq, *(_key_seq(key) + 1 for key in used)])
    seen: set[str] = set()
    milestones: list[Milestone] = []
    for milestone in model.milestones:
        keyed: list[PlanTask] = []
        for task in milestone.tasks:
            key = task.key
            if not key or key in seen:
                key, seq = unit_key(seq), seq + 1
            seen.add(key)
            keyed.append(task.model_copy(update={"key": key}))
        milestones.append(milestone.model_copy(update={"tasks": keyed}))
    return model.model_copy(update={"milestones": milestones, "next_unit_seq": seq})


def carry_over_unit_keys(model: PlanModel, previous: PlanModel) -> PlanModel:
    """作り直した計画に、前の計画のキーを引き継ぐ。番号は前の計画から続け、残りは
    `assign_unit_keys`が振る。作り直しは全体を置き換えるが、同じ作業単位の手順書(段階8)と変更の
    影響の印を付けたままにするため。次の順に1対1で引き継ぐ:

    1. すでに前の計画のキーを持つタスク(書き手が前の版のタスクを指したもの)
    2. タスク名が同じタスク(前の計画で同じ名前が2つあるものは除く)
    3. 種別が同じで、処理・要件の重なり(Jaccard)が大きい順。重なりが無ければ、タスク名が似ている
       もの(`_TITLE_SIMILARITY`以上。前の計画で同じ名前が2つあるものは除く)。再生成でタスク名が
       言い換えられても引き継ぐため。"""
    prev_tasks = [t for m in previous.milestones for t in m.tasks if t.key]
    tasks = [t for m in model.milestones for t in m.tasks]
    counts = Counter(t.title.strip() for t in prev_tasks)
    unique = [t for t in prev_tasks if counts[t.title.strip()] == 1]
    keys: list[str] = ["" for _ in tasks]
    used: set[str] = set()

    def take(index: int, key: str) -> None:
        if not keys[index] and key not in used:
            keys[index] = key
            used.add(key)

    prev_keys = {t.key for t in prev_tasks}
    for i, task in enumerate(tasks):
        if task.key.strip() in prev_keys:
            take(i, task.key.strip())
    by_title = {t.title.strip(): t.key for t in unique}
    for i, task in enumerate(tasks):
        if task.title.strip() in by_title:
            take(i, by_title[task.title.strip()])
    pairs: list[tuple[float, float, int, str]] = []
    for i, task in enumerate(tasks):
        for prev in prev_tasks:
            if keys[i] or prev.key in used or prev.kind != task.kind:
                continue
            overlap = _overlap(task, prev)
            similarity = (
                SequenceMatcher(None, task.title.strip(), prev.title.strip()).ratio()
                if prev in unique
                else 0.0
            )
            if overlap > 0 or similarity >= _TITLE_SIMILARITY:
                pairs.append((overlap, similarity, i, prev.key))
    for _, _, i, key in sorted(pairs, key=lambda p: (-p[0], -p[1], p[2])):
        take(i, key)
    keyed = iter(keys)
    milestones = [
        milestone.model_copy(
            update={"tasks": [t.model_copy(update={"key": next(keyed)}) for t in milestone.tasks]}
        )
        for milestone in model.milestones
    ]
    seq = max(model.next_unit_seq, previous.next_unit_seq)
    return assign_unit_keys(
        model.model_copy(update={"milestones": milestones, "next_unit_seq": seq})
    )


def _overlap(task: PlanTask, other: PlanTask) -> float:
    """2つのタスクの処理・要件の重なり(Jaccard。どちらも空なら 0)。"""
    a = {f"f:{x.strip()}" for x in task.function_ids} | {f"r:{x.strip()}" for x in task.req_ids}
    b = {f"f:{x.strip()}" for x in other.function_ids} | {f"r:{x.strip()}" for x in other.req_ids}
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _key_seq(key: str) -> int:
    """`U-001`形式のキーの番号(形の違うキーは0)。"""
    head, _, number = key.partition("-")
    return int(number) if head == "U" and number.isdigit() else 0


def unit_ids(model: PlanModel) -> list[str]:
    """全単位の ID(計画の並び順。マイルストーンの順、その中のタスクの順)。"""
    return [
        task_id(m, t)
        for m, milestone in enumerate(model.milestones)
        for t in range(len(milestone.tasks))
    ]


def milestone_functions(milestone: Milestone) -> list[str]:
    """マイルストーンで動くようにする処理(タスクの処理を、並び順に重複なく)。"""
    return _clean(f for task in milestone.tasks for f in task.function_ids)


def planned_function_ids(model: PlanModel) -> set[str]:
    """計画のどこかの単位に書かれた処理ID。"""
    found: set[str] = set()
    for milestone in model.milestones:
        found.update(milestone_functions(milestone))
    return found


def unplanned_functions(model: PlanModel, function_ids: Sequence[str]) -> list[str]:
    """どの単位にも書かれていない処理ID(機能一覧の順)。"""
    planned = planned_function_ids(model)
    return [f for f in function_ids if f not in planned]


def is_file_path(path: str) -> bool:
    """パスの最後の区切りに拡張子があるか(無ければディレクトリとみなす。`frontend`など)。"""
    name = path.strip().rstrip("/").rsplit("/", 1)[-1]
    return "." in name.strip(".")


def missing_topics(model: PlanModel) -> list[str]:
    """07 横断事項に必ず書く項目のうち、行の無いもの。"""
    topics = {row.topic.strip() for row in model.crosscutting}
    return [topic for topic in CROSSCUTTING_TOPICS if topic not in topics]


def _clean(values: Iterable[str]) -> list[str]:
    """前後の空白を除き、空と重複を捨てる(順は保つ)。"""
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _modules(values: Iterable[str], module_paths: Sequence[str]) -> list[str]:
    """ファイルの参照を、モジュール一覧のパスにそろえる(段階5の呼び出し先と同じ規則)。
    1行だけに当たるものだけを置き換え、当たらないもの(環境のファイルなど)はそのまま残す。"""
    return _clean(resolve_callee(v, module_paths) for v in values if v.strip())


def normalize_plan(model: PlanModel, module_paths: Sequence[str]) -> PlanModel:
    """AIの下書きを整える。前後の空白・空の行・一覧の重複を除き、モジュールをパスにそろえる。
    依存先の ID と環境・設定のファイルは、空白と重複を除くだけにする(検証が指摘する)。

    - 横断事項は、項目も方針も空の行を捨てる。
    - タスクは、名前が空の行を捨てる。マイルストーンは、名前もタスクも無いものを捨てる。
    - リスクは、リスクも対策も空の行を捨てる。
    """
    crosscutting = [
        CrossCuttingRow(
            topic=row.topic.strip(),
            policy=row.policy.strip(),
            modules=_modules(row.modules, module_paths),
            req_ids=_clean(row.req_ids),
        )
        for row in model.crosscutting
        if row.topic.strip() or row.policy.strip()
    ]
    milestones: list[Milestone] = []
    for milestone in model.milestones:
        tasks = [
            PlanTask(
                key=task.key.strip(),
                kind=task.kind,
                title=task.title.strip(),
                function_ids=_clean(task.function_ids),
                depends_on=_clean(task.depends_on),
                modules=_modules(task.modules, module_paths),
                config_files=_clean(task.config_files),
                req_ids=_clean(task.req_ids),
            )
            for task in milestone.tasks
            if task.title.strip()
        ]
        if not milestone.name.strip() and not tasks:
            continue
        milestones.append(
            Milestone(
                name=milestone.name.strip(),
                goal=milestone.goal.strip(),
                priority=milestone.priority,
                tasks=tasks,
            )
        )
    risks = [
        Risk(risk=row.risk.strip(), mitigation=row.mitigation.strip())
        for row in model.risks
        if row.risk.strip() or row.mitigation.strip()
    ]
    return PlanModel(
        crosscutting=crosscutting,
        milestones=milestones,
        environment=model.environment.strip(),
        risks=risks,
        next_unit_seq=model.next_unit_seq,
    )
