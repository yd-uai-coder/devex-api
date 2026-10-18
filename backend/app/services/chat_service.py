import json
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Purpose
from app.ai.llm.factory import STRUCTURED_METHOD
from app.hearing import (
    CARD_LIMIT,
    POINT_DEFS,
    Card,
    CardAnswerError,
    CardOption,
    JudgedPoint,
    PointDef,
    PointState,
    Scale,
    apply_card_answers,
    as_scale,
    build_summary,
    card_as_text,
    card_targets,
    card_to_dict,
    cards_from_json,
    carry_over_cards,
    compose_answer_message,
    describe_points,
    is_point_key,
    is_required,
    is_sufficient,
    mark_selected,
    merge_judgement,
    missing_labels,
    number_messages,
    open_optional,
    point_def,
    resolve_answers,
    sanitize_cards,
    scale_after,
    scale_card,
    scale_of_answer,
)
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.prompt_template import PromptTemplate
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.hearing_point import HearingPointRepository, states_from_rows
from app.repositories.prompt_template import PromptTemplateRepository
from app.review.rehearing import rehearing_findings_text
from app.schemas.generation import HearingCompletionCheck
from app.schemas.hearing import (
    CardAnswer,
    EvidenceRead,
    HearingPointRead,
    HearingPointsJudgement,
    HearingPointsRead,
    HearingReplyOut,
)
from app.services.errors import (
    DocGenerationInProgressError,
    GenerationFailedError,
    HearingPointNotFoundError,
    HearingPointNotOpenError,
    InvalidCardAnswerError,
    LLMQuotaExceededError,
)
from app.services.llm_resolver import LlmResolver
from app.services.llm_retry import invoke_with_retry

logger = structlog.get_logger(__name__)

_HEARING_SYSTEM_PROMPT = (
    "あなたはシステム開発の要件定義を支援するAIアシスタントです。"
    "ユーザーが最初に入力した概要・実現したいこと・補足(および添付資料の内容)を踏まえ、"
    "目的や課題、コア機能、想定ユーザー、MVPスコープを対話の中で明確にしてください。"
    "初期入力の段階で箇条書きへの分解や機能の構造化は行われていないため、必要に応じて"
    "あなたが提案し、ユーザーの同意・修正を得てください。"
    "返信の際は、ユーザーが確認を求めていない限り、ユーザーが既に提示した内容をそのまま"
    "要約・反復しないでください。要件を確定するために今何が不足しているかを吟味した上で、"
    "その不足点を埋めるための問いかけに絞って簡潔に返信してください"
    "(シンプルな質問、選択肢の提示など)。"
    "要件の確定の確認、ヒアリング内容のまとめ(サマリ)、設計書(要件定義書など)の本文は"
    "チャットに書かないでください。要件がそろったかどうかの判定とまとめの提示はシステムが行い、"
    "設計書は画面のボタンから生成します。"
)

_POINTS_JUDGE_PROMPT = (
    "これまでの対話を読み、下の観点の表の各観点について、状態・決めたこと・根拠を判定してください。"
    "ユーザー側の発言の先頭にある[#n]が、その発言の番号です。\n"
    "状態:\n"
    "・decided: ユーザーが自分の言葉で決めた\n"
    "・adopted: AIが出した提案・選択肢に、ユーザーが同意した\n"
    "・not_applicable: この観点はこのシステムには要らない、とユーザーが答えた\n"
    "・open: まだ決まっていない(聞いていない、答えていない、「決めていない」と答えた)\n"
    "判定の規則:\n"
    "・根拠(evidence)は、その観点を決めたユーザー側の発言の番号だけを挙げる。AIの発言は根拠にしない。\n"
    "・AIが提案・推測しただけで、ユーザーが答えていない観点はopenにする。\n"
    "・decided・adoptedにするのは、その観点の「決まったと言える条件」を満たすときだけ。"
    "一部しか分からない(例: 技術スタックでDBの名前だけ)ならopenにする。"
    "ほかの観点のことを書いた発言を、この観点の根拠にしない。\n"
    "・ユーザーが自分の言葉で内容を書いたら、AIの例や選択肢と似ていてもdecidedにする。"
    "adoptedは、選択肢を選んだ・AIの提案に「それでよい」などと同意しただけの場合。\n"
    "・ユーザーが「おすすめでよい」「おまかせ」のように判断を委ねたとき、直前のAIの提案に"
    "具体的な値・方式(回数・時間・方式の名前など)が無ければopenにする"
    "(値の無い方針に同意しただけでは決まっていない)。\n"
    "・今の状態がopen以外の観点は、ユーザーが言い直した場合だけ変える。\n"
    "・規模がpublic(誰でも登録できるサービス)のとき、securityは、誰がどのデータを見られる・"
    "変えられるか(アクセス制御)が分かるまでdecided・adoptedにしない"
    "(ログインの方法だけではopenのままにする)。\n"
    "・summaryは、決めたことを1文で簡潔に書く(openなら空でよい)。"
    "adoptedなら、同意した提案の具体的な値・方式を書く。ただし技術スタック(tech)は、"
    "ユーザーが挙げた言語・フレームワーク・DB・使うAI(LLM)・置き場所などの名前を1つも落とさずに書く"
    "(設計書の生成はこの要約を読むため)。\n"
    "・scaleは、規模の観点が決まったときだけ、personal(自分と数人)/internal(社内)/"
    "public(誰でも登録できるサービス)のどれかにする。\n"
    "観点の表(キー|観点|場面で聞く問いの例|決まったと言える条件|今の状態|決めたこと):\n"
    "{table}"
)

# 直前のAIの問いの観点を、判定のプロンプトに添える。判定のLLMは16観点を一度に見るので、
# 最新の答えを取りこぼして未決のまま残すことがある(同じカードが付け直され、利用者が同じことを
# 二度答える)。どの観点への答えかをコードが示す
_LATEST_QUESTION_NOTE = (
    "\n直前のAIの問いは、観点 {points} についてのもの。最新のユーザーの発言がその答えなら、"
    "その観点を必ず判定する。"
)

# 観点の表の画面から「対象外」にした観点の、決めたことの文
_SKIPPED_BY_USER_SUMMARY = "後で決める(画面で対象外にした)"

# 「任意項目についてヒアリングを続ける」で、利用者の発言として残す文(判定のLLMは呼ばない)
_CONTINUE_OPTIONAL_MESSAGE = "任意の項目についてもヒアリングを続けてください"

# 最初の発話の後ろにコードが足す文。規模は後の必須を切り替えるので、最初にカードで聞く
_OPENING_SCALE_CARD_LINE = "まず、下のカードで、使う人の規模を選んでください。"

# 完了判定が十分になったときの返信。LLMで書かせず、観点の表から組み立てたまとめを見せることで、
# チャットのまとめと画面の生成ボタンが同じ判定から同時に出るようにする
# 返信のLLMに、選択肢カードの書き方と、カードにしてよい観点を伝える(返信の生成のときだけ足す)
_CARD_INSTRUCTIONS = (
    "\n\n[選択肢カード]\n"
    "下の観点について、問いを1つだけ聞いてください。ほかの観点の質問はしないでください。\n"
    "・選択肢で答えやすい観点なら、選択肢カード(cards)を1枚だけ返し、本文では質問せず、"
    "短い前置きだけを書く。\n"
    "・目的やコア機能のように自由に答える方がよい観点なら、カードは返さず、本文でその観点の"
    "質問を1つだけ書く。\n"
    "・問いは場面で聞く(例: 「夜中や休日に止まっていたら、どのくらいで困りますか」)。"
    "専門用語(可用性・RTO など)で聞かない。\n"
    "・選択肢は2〜4個。各選択肢の結果(result)には、選んだときのコスト・手間・後から変えられるかを"
    "1文で書く。\n"
    "・おすすめ(recommended)は1つだけ選び、その理由(reason)を、これまでの対話の内容に結びつけて"
    "1文で書く。\n"
    "・ユーザーが前の問いで「おすすめでよい」「おまかせ」のように判断を委ねた観点は、"
    "自由に答える観点でも必ず選択肢カードにし、選択肢の名前を具体的な値・方式で書き"
    "(例: 「ログインの失敗は1分あたり5回まで」)、おすすめを付ける。"
    "本文で「おすすめを適用します」のように決めたことにしない。\n"
    "聞く観点(キー: 観点(問いの例)):\n"
    "{targets}"
)
# 規模のカードと、前の返信で答えなかったカードの付け直しは、システムがカードを出す。そのときの指示
_CODE_CARD_NOTE = (
    "\n\n[選択肢カード]\n{label}は、システムが選択肢カードで聞きます。本文では質問せず、"
    "カード(cards)も返さず、短い前置きだけを書いてください。"
)
# 未決の必須の観点が残っている間、返信のAIが完了を宣言しないようにする(完了はシステムが観点の
# 表から判定し、そろえばまとめと生成ボタンを出す。AIが先に「そろった」と書くと、画面と食い違う)
_MISSING_POINTS_NOTE = (
    "この一覧が残っている間は、要件がそろった・設計書を生成できる・生成ボタンを押す、などとは"
    "書かないでください。一覧の観点を聞いてください。"
)
# それでも返信のAIが完了を宣言した文(_drop_completion_claimsが除く)
_COMPLETION_CLAIM = re.compile(
    r"(揃|そろ)(いました|った)|すべて.*(確認|把握)(でき|し)ました|準備が(整|ととの)い"
    r"|設計書を生成(して|でき|する)|生成(の)?ボタン|ボタンを押"
)
# 利用者が判断を AI に委ねた発言。委ねられた観点は、値の無い方針のまま決まったことにせず、
# 具体的な値・方式の選択肢カードで聞き直す(判定は open に残す。_POINTS_JUDGE_PROMPT の規則)
_DELEGATION = re.compile(r"おすすめ|お勧め|おまかせ|お任せ|任せ|推奨")
# 委ねられたときに、返信のプロンプトの最後にコードが足す指示(返信の LLM は、カードを出さずに
# 本文で「おすすめを適用します」と書いてしまうことがあるため、観点を名指しする)
_DELEGATED_NOTE = (
    "利用者は直前の問いの判断を AI に委ねました。{label}を、必ず選択肢カード(cards)1枚で"
    "聞いてください。選択肢の名前は具体的な値・方式(回数・時間・長さ・方式の名前)で書き、"
    "おすすめを1つ付けます。本文は短い前置きだけにし、おすすめを適用した・決めた、とは書かないで"
    "ください。"
)
# 設計レビューの指摘で聞き直している観点を、カードで聞くときに返信のプロンプトの最後に足す指示。
# 1枚のカードで指摘の論点の一部しか値にならないこと(「など」で省く)があったため、指摘の本文を
# 名指しして、論点ごとに値を書かせる
_REHEARING_CARD_NOTE = (
    "{label}は、設計レビューの次の指摘で聞き直している観点です。\n{findings}\n"
    "選択肢カード(cards)で聞くなら、どの選択肢の名前にも、指摘が挙げた対象(例: ログイン失敗・"
    "登録・チャット・生成)それぞれの具体的な値・方式を書いてください(「など」で省かない。"
    "1つの選択肢だけに書いて、ほかの選択肢で落とさない)。1枚で聞ききれないときは、まだ値の無い"
    "対象をこのカードで聞き、残りは次の問いで聞きます。"
)
# 聞き直している観点のカードに利用者が答えたときに、判定のプロンプトに足す指示。カードの答えを
# そのまま決めたことにすると、聞き直しの前の要約が選択肢の文で置き換わり、指摘の論点のうち
# カードに無かったものも決まったことになるため、判定の LLM に前の要約と合わせて書き直させる
_REHEARING_ANSWER_NOTE = (
    "\n観点 {key}({label})は、設計レビューの指摘で聞き直している観点で、最新のユーザーの発言は"
    "そのカードの答え(「{answer}」)です。\n指摘: {findings}\n聞き直しの前の要約: {summary}\n"
    "この観点を必ず判定する。まず、指摘が挙げた対象(例: ログイン失敗・登録・チャット・生成)を"
    "1つずつ見て、答えにも聞き直しの前の要約にも具体的な値・方式が無いものが1つでもあれば open に"
    "する。すべてに値・方式があれば adopted にする(カードの選択なので decided にしない)。"
    "summaryは、どちらの場合も、聞き直しの前の要約のうち答えと食い違わない内容を落とさず、答えの"
    "値と合わせて書く。"
)
# 返信に問いもカードも無いときに、コードが足す次の問い
_NEXT_QUESTION_TEMPLATE = "次に、{label}について教えてください。(例: {question})"
# 「？」が無くても、利用者に答えを求める文(依頼・問いの言い切り)なら問いとみなす
_QUESTION_ENDING = re.compile(
    r"(ください|ますか|ませんか|ですか|でしょうか)(?=[。．.!！」)）]|\s|$)"
)
_EMPTY_REPLY_WITH_CARDS = "次の点を、カードから選ぶか、チャットで答えてください。"

_SUMMARY_REPLY_TEMPLATE = (
    "ヒアリングの内容をまとめました。\n\n{summary}\n\n"
    "この内容でよければ「この内容で設計書を生成する」を押してください。"
    "直したい点があれば、チャットで伝えてください。"
)

_OPENING_TURN_PROMPT = (
    "これはこのプロジェクトのヒアリング対話における、あなたの最初の返信です。"
    "ユーザーから提示された内容をそのまま繰り返さず、次の形式で日本語で回答してください。\n"
    "1. 1行目:「以上を元に詳細のヒアリングを進めていきます。」という一文だけを書く\n"
    "2. 次の行に見出し「【確認したい事】」を書く\n"
    "3. これから対話の中で確認していきたい点を3つ、「・」で始まる箇条書きで簡潔に列挙する\n"
    "この形式以外の前置きや締めの言葉は書かないでください。"
    "最初の問いの聞き方は、システムプロンプトの[選択肢カード]の指示に従ってください。"
)


class ChatService:
    """プロジェクトのヒアリングチャット(対話生成・完了判定)を担当するサービス。

    既存の汎用デモ(app/ai/graph/)とは異なりLangGraphを使わない: ヒアリングフローは
    「毎回LLM応答生成→完了判定」という一本道の処理であり、真の分岐を持つLangGraphの
    StateGraphを導入するのは過剰なため、素のLangChainメッセージで組み立てる。
    """

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._chat_histories = ChatHistoryRepository(session)
        self._prompt_templates = PromptTemplateRepository(session)
        self._hearing_points = HearingPointRepository(session)


    async def _chat_llm(self, project: Project, llm):
        """渡された LLM(テストの差し替え)か、プロジェクトの持ち主のチャットの用途の LLM。"""
        if llm is not None:
            return llm
        return (await LlmResolver(self._session).resolve(project.user_id, Purpose.CHAT)).llm

    async def list_history(self, project_id: uuid.UUID) -> list[ChatHistory]:
        """プロジェクトのチャット履歴を送信日時の昇順(発生順)で取得する。"""
        return await self._chat_histories.list_for_project(project_id)

    async def stream_reply(
        self,
        project: Project,
        *,
        user_message: str,
        card_answers: Sequence[CardAnswer] = (),
        continue_optional: bool = False,
        llm=None,
    ) -> AsyncIterator["str | ReplyCards"]:
        """ユーザーメッセージ(と選択肢カードへの回答)を永続化し、先に観点の表の判定を行ってから、
        その結果に応じたAI応答を返す。返信の本文(str)を返した後、選択肢カードがあれば
        `ReplyCards`を返す。AI応答はchat_historiesへ、観点の状態はhearing_pointsへ、完了判定の
        結果はprojects.hearing_checkへ保存する。

        - カードへの回答は、LLMを呼ばずにコードが「AIの提案を採用」と記録する。選んだ内容と
          文は1つの発言にまとめ、その発言を根拠にする(app/hearing/cards.py)。ただし、設計
          レビューの指摘で聞き直している観点への回答は、判定の LLM に回し、聞き直しの前の要約と
          合わせて決めたことを書かせる(`_REHEARING_ANSWER_NOTE`。指摘の論点が残れば open)。
          聞き直している観点を次に聞くときは、指摘を名指しして論点ごとの値を求める
          (`_REHEARING_CARD_NOTE`)。
        - 完了は、必須の観点がすべて未決以外かどうかをコードが決める(app/hearing/points.py)。
        - 十分なら、LLMで返信を作らず、観点の表から組み立てたまとめを決まった形で返す(まとめと
          生成ボタンを同じ判定から同時に出すため)。
        - 足りなければ、未決の必須の観点とカードにしてよい観点を渡し、本文とカードを構造化出力で
          1回で作る(本文はまとめて1回で返す。トークンごとには流さない)。
        - 利用者が判断を委ねた(「おすすめでよい」)ときは、次に聞く観点を具体的な値の選択肢カードで
          聞くよう、コードが返信の指示に添える(`_DELEGATED_NOTE`。判定は値の無い同意を open に
          残す)。
        - 前の発言のカードのうち答えなかったもの(観点がまだ未決)は、次の返信にコードが付け直す。
          カードが1枚も無く、聞くべき観点が残っているときは、次の問いをコードが本文に足す
          (設問の取りこぼしと、問いの無い返信で止まったように見えるのを防ぐ)。
        - 必須がそろった後は、利用者が任意の観点を続けると選んだとき(continue_optional、または
          任意の観点のカードに答えたとき)だけ、未決の任意の観点を聞く。それ以外はまとめを返す。
        - 判定に失敗したら、観点の表は前のまま残し、保存済みの完了判定は消す(古い判定で生成ボタンが
          残らないように。表があって完了判定が無い状態を、画面は「表が直近の発言を反映していない」と
          読む)。チャット自体は止めない。

        completed(生成済み)のプロジェクトへ新規メッセージが送られた場合はrevising(修正中)へ
        遷移させる(ユーザーがヒアリング内容を修正し、再生成する意思を示したものとみなす)。
        """
        if project.status == "completed":
            project.status = "revising"
        # 判定のLLMを呼ぶか: 利用者が文を書いたとき、またはまだ判定が済んでいないとき(作成時の
        # 判定の失敗・直近の判定の失敗・以前の判定のプロジェクト)。カードの回答だけ・「任意を
        # 続ける」だけの送信は、読むべき新しい文が無いので呼ばない(選んだ観点はコードが記録する)
        judged = project.hearing_check is not None and bool(
            await self._hearing_points.list_for_project(project.id)
        )
        needs_judge = bool(user_message.strip()) or not judged
        previous_cards, answered, reheard = await self._record_user_turn(
            project, user_message, card_answers, continue_optional=continue_optional
        )
        # 聞き直している観点へのカードの答えは、判定の LLM が前の要約と合わせて決める
        needs_judge = needs_judge or bool(reheard)

        # 「or」の理由：テスト時に本物のLLMを叩かせないための差し替え(DI)
        llm = await self._chat_llm(project, llm)
        states: dict[str, PointState] | None
        if needs_judge:
            try:
                states = await self.judge_points(project, llm=llm, reheard=reheard)
            except GenerationFailedError:
                logger.warning("hearing_check_failed")
                states = None
                if reheard:
                    # 判定に失敗しても答えは失わない(今までどおり、選んだ選択肢を記録する)
                    await self._record_card_answers(project, reheard)
        else:
            states = await self._load_states(project)
        if states is None:
            project.hearing_check = None
            states = await self._load_states(project)
        else:
            check = _completion_from(states, project.hearing_scale)
            project.hearing_check = check.model_dump()
            if check.is_sufficient:
                scale = as_scale(project.hearing_scale)
                optional = open_optional(states, scale)
                wants_optional = continue_optional or any(
                    not is_required(card.point_key, scale) for card, _ in answered
                )
                if not (wants_optional and optional):
                    reply = _SUMMARY_REPLY_TEMPLATE.format(summary=check.summary.strip())
                    await self._chat_histories.add(
                        project_id=project.id, sender="ai", message=reply
                    )
                    await self._session.commit()
                    yield reply
                    return

        # 次に聞く問いは1つ。前の返信で答えなかったカード(観点がまだ未決)があれば、それを付け直す
        carried = carry_over_cards(previous_cards, answered, states, lambda: str(uuid.uuid4()))[:1]
        targets = _next_targets(states, as_scale(project.hearing_scale))
        if carried:
            targets = [point_def(carried[0].point_key)]
        delegated = (
            not carried
            and bool(targets)
            and targets[0].key != "scale"
            and bool(_DELEGATION.search(user_message))
        )
        notes = [_DELEGATED_NOTE.format(label=targets[0].label)] if delegated else []
        rehearing = (
            await self._rehearing_note(project, states, targets[0])
            if targets and not carried
            else None
        )
        if rehearing is not None:
            notes.append(rehearing)
        message, cards = await self._compose_reply(
            project,
            questions=describe_points(targets),
            targets=targets,
            code_card=carried[0] if carried else None,
            extra_prompt="\n".join(notes) or None,
            llm=llm,
        )
        if not is_sufficient(states, as_scale(project.hearing_scale)):
            message = _drop_completion_claims(message)
        message = _ensure_question(message, cards, targets)
        await self._chat_histories.add(
            project_id=project.id,
            sender="ai",
            message=message,
            cards=[card_to_dict(card) for card in cards] or None,
        )
        await self._session.commit()
        yield message
        if cards:
            yield ReplyCards(cards=[card_to_dict(card) for card in cards])

    async def _record_user_turn(
        self,
        project: Project,
        user_message: str,
        card_answers: Sequence[CardAnswer],
        *,
        continue_optional: bool = False,
    ) -> tuple[list[Card], list[tuple[Card, int]], list[tuple[Card, int]]]:
        """利用者の発言を保存する。カードへの回答があれば、選んだ内容を発言の本文に足し、カードに
        選択を書き込み、選ばれた観点を「AIの提案を採用」にする(根拠はこの発言)。ただし、設計
        レビューの指摘で聞き直している観点(`reopened_after`)への回答は記録せず、判定に回す。

        選べるのは最新のAIの発言のカードのうち未回答のものだけ(古い画面から送られた回答は拒む)。
        返り値は(最新のAIの発言のカード、今回の回答、そのうち判定に回す回答)。前の2つは
        答えなかったカードの付け直しに使う。
        「任意を続ける」だけの送信は、決まった文を利用者の発言として残す。
        """
        answered: list[tuple[Card, int]] = []
        history = await self._chat_histories.list_for_project(project.id)
        latest_ai = next((e for e in reversed(history) if e.sender == "ai"), None)
        cards = cards_from_json(latest_ai.cards) if latest_ai is not None else []
        if card_answers:
            try:
                answered = resolve_answers(cards, {a.card_id: a.option for a in card_answers})
            except CardAnswerError as exc:
                raise InvalidCardAnswerError(str(exc)) from exc

        text = compose_answer_message(answered, user_message)
        if not text and continue_optional:
            text = _CONTINUE_OPTIONAL_MESSAGE
        entry = await self._chat_histories.add(project_id=project.id, sender="user", message=text)
        if not answered or latest_ai is None:
            return cards, answered, []
        # JSONの列は中身の書き換えを検知しないため、新しいリストを代入する
        latest_ai.cards = [card_to_dict(card) for card in mark_selected(cards, answered)]
        previous = await self._load_states(project)
        reheard = [
            (card, option)
            for card, option in answered
            if previous[point_def(card.point_key).key].reopened_after is not None
        ]
        recorded = [answer for answer in answered if answer not in reheard]
        if recorded:
            states = apply_card_answers(previous, recorded, str(entry.id))
            await self._hearing_points.save_states(project.id, states.values())
        if (scale := scale_of_answer(answered)) is not None:
            project.hearing_scale = scale
        return cards, answered, reheard

    async def _record_card_answers(
        self, project: Project, answered: Sequence[tuple[Card, int]]
    ) -> None:
        """カードの回答を「AIの提案を採用」として記録する(根拠は最新の利用者の発言)。"""
        history = await self._chat_histories.list_for_project(project.id)
        latest_user = next(e for e in reversed(history) if e.sender == "user")
        states = apply_card_answers(await self._load_states(project), answered, str(latest_user.id))
        await self._hearing_points.save_states(project.id, states.values())

    async def _rehearing_note(
        self, project: Project, states: Mapping[str, PointState], target: PointDef
    ) -> str | None:
        """次に聞く観点が聞き直しの最中なら、その指摘を名指しした返信の指示。そうでなければ None。
        """
        findings = await self._rehearing_findings(project, states, target.key)
        if not findings:
            return None
        return _REHEARING_CARD_NOTE.format(label=target.label, findings=findings)

    async def _rehearing_findings(
        self, project: Project, states: Mapping[str, PointState], key: str
    ) -> str:
        """聞き直している観点の指摘(聞き直しの問いかけから取り出す)。聞き直していなければ空。"""
        reopened = states[key].reopened_after
        if reopened is None:
            return ""
        history = await self._chat_histories.list_for_project(project.id)
        message = next((e.message for e in history if str(e.id) == reopened), "")
        return rehearing_findings_text(message, key)

    async def _compose_reply(
        self,
        project: Project,
        *,
        questions: Sequence[str],
        targets: Sequence[PointDef],
        code_card: Card | None = None,
        extra_prompt: str | None = None,
        llm,
    ) -> tuple[str, list[Card]]:
        """返信の本文と選択肢カード(最大1枚)を、構造化出力の1回で作る。

        カードをシステムが出すとき(前の返信のカードの付け直し`code_card`、規模のカード)は、LLMには
        前置きの本文だけを書かせる。LLMのカードは、聞く観点のものだけを確かめてから使う
        (sanitize_cards)。単発呼び出しのため、一時的な失敗はinvoke_with_retryでリトライする。
        """
        if code_card is None and any(d.key == "scale" for d in targets):
            code_card = scale_card(str(uuid.uuid4()))
        llm_targets = [] if code_card is not None else list(targets)
        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        messages = _build_messages(
            history,
            project,
            template,
            missing_points=questions,
            card_targets=llm_targets,
            code_card_label=code_card.label if code_card is not None else None,
        )
        if extra_prompt is not None:
            messages.append(HumanMessage(content=extra_prompt))
        structured_llm = llm.with_structured_output(HearingReplyOut, method=STRUCTURED_METHOD)

        async def _call() -> HearingReplyOut:
            result = await structured_llm.ainvoke(messages)
            assert isinstance(result, HearingReplyOut)
            return result

        result = await invoke_with_retry(_call, messages=messages)
        if code_card is not None:
            cards = [code_card]
        else:
            cards = sanitize_cards(
                (
                    Card(
                        id=str(uuid.uuid4()),
                        point_key=card.point_key,
                        question=card.question,
                        options=tuple(CardOption(o.label, o.result) for o in card.options),
                        recommended=card.recommended,
                        reason=card.reason,
                    )
                    for card in result.cards
                ),
                [d.key for d in llm_targets],
            )
        message = result.message.strip()
        if not message:
            if not cards:
                raise GenerationFailedError("ヒアリングの返信が空でした")
            message = _EMPTY_REPLY_WITH_CARDS
        return message, cards

    async def judge_points(
        self, project: Project, *, llm=None, reheard: Sequence[tuple[Card, int]] = ()
    ) -> dict[str, PointState]:
        """これまでの対話から、観点ごとの状態と根拠をLLMに判定させ、観点の表に取り込んで保存する
        (hearing_points・projects.hearing_scale。commitは呼び出し側)。取り込んだ後の表を返す。

        `reheard`は、聞き直している観点へのカードの回答。観点・答え・指摘・聞き直しの前の要約を
        名指しして、前の要約と合わせた決めたことを書かせる(`_REHEARING_ANSWER_NOTE`)。

        LLMには利用者側の発言に番号を付けて見せ、返った番号をchat_historiesのidに直す。根拠の
        無い「決定」は取り込まない(merge_judgement)。完了かどうかはLLMに聞かない。

        単発呼び出し(ストリーミングではない)のため、一時的な失敗はinvoke_with_retryでリトライする
        """
        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        previous = await self._load_states(project)
        numbered = number_messages([(str(entry.id), entry.sender) for entry in history])
        numbers = {message_id: number for message_id, number, _ in numbered}
        prompt = _POINTS_JUDGE_PROMPT.format(table=_points_table(previous))
        latest_ai = next((e for e in reversed(history) if e.sender == "ai"), None)
        asked = [card.point_key for card in cards_from_json(latest_ai.cards if latest_ai else None)]
        if latest_ai is not None and not asked:
            # 文で聞いた問いは記録していないが、返信と同じ計算で、前の表から聞いた観点が分かる
            asked = [d.key for d in _next_targets(previous, as_scale(project.hearing_scale))]
        if asked:
            points = "・".join(f"{key}({point_def(key).label})" for key in asked)
            prompt += _LATEST_QUESTION_NOTE.format(points=points)
        for card, option in reheard:
            key = point_def(card.point_key).key
            prompt += _REHEARING_ANSWER_NOTE.format(
                key=key,
                label=point_def(key).label,
                answer=card.options[option].label,
                findings=await self._rehearing_findings(project, previous, key) or "(不明)",
                summary=previous[key].summary or "なし",
            )
        messages = [
            *_build_messages(history, project, template, numbers=numbers),
            HumanMessage(content=prompt),
        ]

        llm = await self._chat_llm(project, llm)
        structured_llm = llm.with_structured_output(
            HearingPointsJudgement, method=STRUCTURED_METHOD
        )

        async def _call() -> HearingPointsJudgement:
            result = await structured_llm.ainvoke(messages)
            assert isinstance(result, HearingPointsJudgement)
            return result

        result = await invoke_with_retry(_call, messages=messages)
        ids_by_number = {number: message_id for message_id, number in numbers.items()}
        user_messages = {str(entry.id) for entry in history if entry.sender == "user"}
        after_reopen = _messages_after_reopen(history, previous)
        judged = [
            JudgedPoint(
                key=point.key,
                status=point.status,
                summary=point.summary,
                evidence=tuple(
                    dict.fromkeys(ids_by_number[n] for n in point.evidence if n in ids_by_number)
                ),
            )
            for point in result.points
        ]
        states = merge_judgement(
            previous, judged, user_messages=user_messages, after_reopen=after_reopen
        )
        if reheard:
            states = _adopted_when_only_card(states, reheard, str(history[-1].id))
        project.hearing_scale = scale_after(
            states, as_scale(project.hearing_scale), result.scale
        )
        await self._hearing_points.save_states(project.id, states.values())
        return states

    async def points_view(self, project: Project) -> HearingPointsRead:
        """チャット画面の観点の表(LLMは呼ばない)。表がまだ無いプロジェクトは、全観点を未決で返す。"""
        rows = await self._hearing_points.list_for_project(project.id)
        states = states_from_rows(rows)
        scale = as_scale(project.hearing_scale)
        history = await self._chat_histories.list_for_project(project.id)
        labels = {
            message_id: label
            for message_id, _, label in number_messages(
                [(str(entry.id), entry.sender) for entry in history]
            )
        }
        stale = bool(rows) and project.hearing_check is None
        return HearingPointsRead(
            scale=scale,
            legacy=not rows and project.hearing_check is not None,
            stale=stale,
            is_sufficient=bool(rows) and not stale and is_sufficient(states, scale),
            points=[
                HearingPointRead(
                    key=d.key,
                    kind=d.kind,
                    label=d.label,
                    question=d.question,
                    required=is_required(d.key, scale),
                    status=states[d.key].status,
                    summary=states[d.key].summary,
                    evidence=[
                        EvidenceRead(message_id=uuid.UUID(message_id), label=labels[message_id])
                        for message_id in states[d.key].evidence
                        if message_id in labels
                    ],
                )
                for d in POINT_DEFS
            ],
        )

    async def mark_point_not_applicable(self, project: Project, key: str) -> HearingPointsRead:
        """観点の表の画面から、未決の観点を「対象外(後で決める)」にする(LLMは呼ばない)。

        判定のAIが決められない観点で、ヒアリングが先へ進めなくなるのを避けるための利用者の操作。
        根拠の発言は持たない(利用者が画面で決めたため)。後でチャットで答えれば、判定のAIが
        言い直しとして上書きできる。完了判定はその場で表から作り直すので、必須がそろえば
        生成のボタンが出る。
        """
        if not is_point_key(key):
            raise HearingPointNotFoundError("観点が見つかりません")
        if project.status == "generating":
            raise DocGenerationInProgressError("設計書の生成中は観点を変えられません")
        states = await self._load_states(project)
        if states[key].status != "open":
            raise HearingPointNotOpenError("未決の観点だけを対象外にできます")
        states[key] = PointState(point_def(key).key, "not_applicable", _SKIPPED_BY_USER_SUMMARY)
        await self._hearing_points.save_states(project.id, states.values())
        if key == "scale":
            project.hearing_scale = None  # 規模を対象外にしたら、個人・小規模の列で数える
        if project.status == "completed":
            project.status = "revising"
        project.hearing_check = _completion_from(states, project.hearing_scale).model_dump()
        await self._session.commit()
        return await self.points_view(project)

    async def reopen_points(
        self, project: Project, keys: Sequence[str], message: str
    ) -> HearingPointsRead:
        """設計レビューの指摘で、観点を未決に戻して聞き直す(LLMは呼ばない)。聞き直しの文を AI の
        発言としてチャットに残し、戻した観点にその発言を印として付ける(それより後の答えでないと、
        決まったことに戻さない)。完成したプロジェクトは「修正中」にし、完了判定は表から作り直す
        (必須の観点が未決になれば、生成のボタンは出ない)。生成中は409。"""
        if project.status == "generating":
            raise DocGenerationInProgressError("設計書の生成中は観点を変えられません")
        states = await self._load_states(project)
        entry = await self._chat_histories.add(project_id=project.id, sender="ai", message=message)
        for key in keys:
            if not is_point_key(key):
                raise HearingPointNotFoundError("観点が見つかりません")
            # 決めたことは残す(聞き直しの答えを、前の要約と合わせて判定するため)
            states[key] = PointState(
                point_def(key).key, "open", states[key].summary, reopened_after=str(entry.id)
            )
        await self._hearing_points.save_states(project.id, states.values())
        if "scale" in keys:
            project.hearing_scale = None
        if project.status == "completed":
            project.status = "revising"
        project.hearing_check = _completion_from(states, project.hearing_scale).model_dump()
        await self._session.commit()
        return await self.points_view(project)

    async def _load_states(self, project: Project) -> dict[str, PointState]:
        return states_from_rows(await self._hearing_points.list_for_project(project.id))

    def stored_completion(self, project: Project) -> HearingCompletionCheck:
        """直近の発言で行ったヒアリング完了判定の結果を返す(LLMは呼ばない)。まだ判定していない、
        または直近の判定に失敗したプロジェクトは「十分でない」とする。観点の表を持たない以前の
        プロジェクトは、保存してある5条件の判定をそのまま返す。"""
        if project.hearing_check is None:
            return HearingCompletionCheck(is_sufficient=False, summary="", missing_points=[])
        return HearingCompletionCheck.model_validate(project.hearing_check)

    async def generate_opening_reply(self, project: Project, *, llm=None) -> str:
        """ヒアリング開始直後、ユーザー発話を待たずにAIの最初の発話を生成し永続化する
        (docs/external_design.md SCR-004 2.3節: 「AIの最初の発話は初期ヒアリング入力の
        内容を踏まえた理解の要約と確認質問から始まる」)。プロジェクト作成直後に一度だけ
        呼ばれる想定。失敗時は例外を伝播させる(致命的でない扱いは呼び出し側=ルート層で行う)。

        先に、初期入力・添付資料・開発環境の希望を観点の判定にかけ、書いてある観点を表に埋める
        (書いてあることを聞き直さないため)。判定に失敗しても最初の発話は作る(最初の送信で
        判定し直す)。最初の発話には、判定後の表で次に聞く観点の問いを1つ付ける(規模が未決なら
        規模のカード)。単発呼び出しのため、invoke_with_retryでリトライする。"""
        llm = await self._chat_llm(project, llm)
        try:
            states = await self.judge_points(project, llm=llm)
            project.hearing_check = _completion_from(states, project.hearing_scale).model_dump()
        except (GenerationFailedError, LLMQuotaExceededError):
            logger.warning("opening_hearing_check_failed")
            states = await self._load_states(project)
        targets = _next_targets(states, as_scale(project.hearing_scale))
        reply, cards = await self._compose_reply(
            project,
            questions=describe_points(targets),
            targets=targets,
            extra_prompt=_OPENING_TURN_PROMPT,
            llm=llm,
        )
        if any(card.point_key == "scale" for card in cards):
            reply = f"{reply.rstrip()}\n\n{_OPENING_SCALE_CARD_LINE}"
        reply = _ensure_question(reply, cards, targets)
        await self._chat_histories.add(
            project_id=project.id,
            sender="ai",
            message=reply,
            cards=[card_to_dict(card) for card in cards] or None,
        )
        await self._session.commit()
        # 判定でプロジェクトの列(規模・完了判定)を更新したので、commit で失効した属性を読み直す
        # (呼び出し側がそのまま応答のスキーマに詰めるため)
        await self._session.refresh(project)
        return reply

    async def _load_template(self, project: Project) -> PromptTemplate | None:
        """project.template_idが設定されている場合、対応するPromptTemplateを取得する
        (未設定、または存在しないIDの場合はNoneを返し、通常のヒアリングプロンプトのみを使う)。"""
        if project.template_id is None:
            return None
        return await self._prompt_templates.get_by_id(project.template_id)


def _build_messages(
        history: list[ChatHistory],
        project: Project,
        template: PromptTemplate | None = None,
        *,
        missing_points: Sequence[str] = (),
        card_targets: Sequence[PointDef] = (),
        code_card_label: str | None = None,
        numbers: Mapping[str, int] | None = None,
) -> list[BaseMessage]:
    """chat_historiesの行(sender+message)を、LangChainのメッセージ列に変換する。

    sender='user'/'intake'/'attachment'(初期ヒアリング入力・添付ファイル抽出結果)はHumanMessage、
    sender='ai'はAIMessageとして扱う。sender='others'(生成後の自己診断結果)はヒアリング中の
    対話には含めない(生成完了後にのみ現れるため、通常この時点では存在しない)。

    environment(開発環境の希望)は`chat_histories`には保存されない(チャット画面には表示
    しない、docs/external_design.md参照)ため、`project.intake`から直接読み取ってHumanMessage
    として注入する。ヒアリング完了判定の5条件目「技術的な制約・希望の確認」の材料として、
    表示の有無に関わらずLLMには常に渡す。

    missing_points(観点の表で未決の必須の観点)は、返信の生成のときだけシステムプロンプトに
    足す。AIが次に何を聞くべきかを、完了判定と同じ基準にそろえるため。

    card_targets(聞く観点)・code_card_label(システムがカードを出す観点の名前)も、返信の生成の
    ときだけ渡す。AIの発言に選択肢カードがあれば、その問いと選択肢を文にして発言の後ろに足す
    (何を聞いたかを、判定・返信のLLMが読めるように)。

    numbers(発言のid→番号)は、観点の判定のときだけ渡す。利用者側の発言の先頭に`[#n]`を付け、
    LLMが根拠を番号で返せるようにする。開発環境の希望は初期入力の番号で見せる(初期入力の一部のため)。
    """
    system_prompt = _HEARING_SYSTEM_PROMPT
    if template is not None:
        system_prompt += f"\n\n[選択されたテンプレート: {template.name}]\n{template.system_prompt}"
    if missing_points:
        lines = "\n".join(f"- {point}" for point in missing_points)
        system_prompt += (
            f"\n\n[まだ確認できていない観点]\n{lines}\n{_MISSING_POINTS_NOTE}"
        )
    if card_targets:
        targets = "\n".join(
            f"- {d.key}: {d.label}(例: {d.question})。決まったと言える条件: {d.done_when}"
            for d in card_targets
        )
        system_prompt += _CARD_INSTRUCTIONS.format(targets=targets)
    if code_card_label is not None:
        system_prompt += _CODE_CARD_NOTE.format(label=code_card_label)
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    numbers = numbers or {}

    def _prefix(entry_id: str | None) -> str:
        number = numbers.get(entry_id) if entry_id is not None else None
        return f"[#{number}] " if number is not None else ""

    environment = (project.intake or {}).get("environment")
    if environment:
        intake_id = next((str(e.id) for e in history if e.sender == "intake"), None)
        messages.append(
            HumanMessage(
                content=f"{_prefix(intake_id)}[開発環境の希望]\n"
                f"{json.dumps(environment, ensure_ascii=False)}"
            )
        )
    for entry in history:
        if entry.sender == "ai":
            cards = cards_from_json(entry.cards)
            content = "\n\n".join([entry.message, *(card_as_text(card) for card in cards)])
            messages.append(AIMessage(content=content))
        elif entry.sender in ("user", "intake", "attachment"):
            messages.append(HumanMessage(content=_prefix(str(entry.id)) + entry.message))
    return messages


def _next_targets(states: Mapping[str, PointState], scale: Scale | None) -> list[PointDef]:
    """次に聞く観点(1つ)。必須が未決なら必須から(規模が未決なら規模)、必須がそろっていれば
    未決の任意から(任意を続けるときだけここに来る)。"""
    if not is_sufficient(states, scale):
        return card_targets(states, scale)
    return open_optional(states, scale)[:CARD_LIMIT]


def _drop_completion_claims(message: str) -> str:
    """未決の必須が残っているのに、返信の AI が「そろった」「生成できる」と書いた文を除く。
    プロンプトで止めても書くことがあり、画面(生成ボタンが出ない)と食い違うため。"""
    sentences = re.findall(r"[^。！!\n]*(?:[。！!]|\n|$)", message)
    kept = [s for s in sentences if not _COMPLETION_CLAIM.search(s)]
    return "".join(kept).strip()


def _ensure_question(message: str, cards: Sequence[Card], targets: Sequence[PointDef]) -> str:
    """カードが無く、聞くべき観点が残っているのに、返信の本文に問いが無いときは、次の問いを足す。
    返信のAIが問いを書き忘れると、利用者には次に何を答えるかが見えず、止まったように見えるため。"""
    if cards or not targets or _has_question(message):
        return message
    point = targets[0]
    question = _NEXT_QUESTION_TEMPLATE.format(label=point.label, question=point.question)
    return f"{message}\n\n{question}"


def _has_question(message: str) -> bool:
    """返信の本文に、利用者へ答えを求める文があるか(「？」、または依頼・問いの言い切り)。"""
    return "？" in message or "?" in message or _QUESTION_ENDING.search(message) is not None


@dataclass(frozen=True)
class ReplyCards:
    """stream_replyが返信の本文の後に返す、選択肢カード(`card_to_dict`の形)。ルートはSSEの
    `event: cards`で送る。"""

    cards: list[dict]


def _points_table(states: Mapping[str, PointState]) -> str:
    """判定のプロンプトに埋める観点の表(1行1観点)。"""
    return "\n".join(
        f"{d.key}|{d.label}|{d.question}|{d.done_when}|{states[d.key].status}|"
        f"{states[d.key].summary}"
        for d in POINT_DEFS
    )


def _adopted_when_only_card(
    states: Mapping[str, PointState], reheard: Sequence[tuple[Card, int]], answer_id: str
) -> dict[str, PointState]:
    """聞き直しのカードの答えだけを根拠に「決定」と判定された観点を「採用」に直す(カードの選択は
    AI の提案への同意。判定の LLM が決定と書くことがあった)。"""
    result = dict(states)
    for card, _ in reheard:
        key = point_def(card.point_key).key
        state = result[key]
        if state.status == "decided" and set(state.evidence) <= {answer_id}:
            result[key] = replace(state, status="adopted")
    return result


def _messages_after_reopen(
    history: Sequence[ChatHistory], states: Mapping[str, PointState]
) -> dict[str, set[str]]:
    """未決に戻した観点ごとの、聞き直しの発言より後の利用者の発言の id(履歴の順で数える)。"""
    position = {str(entry.id): i for i, entry in enumerate(history)}
    result: dict[str, set[str]] = {}
    for key, state in states.items():
        if state.reopened_after is None:
            continue
        since = position.get(state.reopened_after, -1)
        result[key] = {
            str(entry.id)
            for i, entry in enumerate(history)
            if i > since and entry.sender == "user"
        }
    return result


def _completion_from(states: Mapping[str, PointState], scale: str | None) -> HearingCompletionCheck:
    """観点の表から完了判定を作る(LLMは呼ばない)。十分ならまとめを付ける。"""
    sufficient = is_sufficient(states, as_scale(scale))
    return HearingCompletionCheck(
        is_sufficient=sufficient,
        summary=build_summary(states, as_scale(scale)) if sufficient else "",
        missing_points=missing_labels(states, as_scale(scale)),
    )
