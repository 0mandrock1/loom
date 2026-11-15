<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=9cbe1bbc3b8361789f5d82dc35b67597e77e8dbf model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントが保存した各メモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいでそれらのシンボルを追跡し、対象のコードが変わるとメモリにフラグを立てます。デフォルトでは、紐づくコードがすべて失われたメモリは検索から外れ、古くなった教訓がエージェントに届かなくなります。この機能は Loom がインデックスしているリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **教訓が、その対象のコードに紐づきます。** リポジトリのタグが付いたメモリ（Claude Code と Codex のプラグインはセッションにタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、バッククォートで名指しされ、ちょうど 1 か所に解決される各定義にリンクされます（リストされたファイル内では、裸の単語で名指しされた定義にもリンクされます）。また、それらの定義を1つも含まない、名指しされた各ファイルにもリンクされます。まだ trunk にない修飾名は、`memory_code_links_pending_days`（14）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リンクはリファクタリングに追従します。** インデックスされるコミットごとに、各リンクの状態は `fresh`、`moved`、`changed`（変更したコミットを示します）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。
- **古くなったメモリを、選んだ方法で扱えます。** 名前空間の `code_change_policy` により、紐づくコードがすべて失われたメモリを検索から外す（`retire_removed`、デフォルト）、フラグを立てるだけにする（`flag`）、またはモデルが主張を古いコードと新しいコードに照らして確認し、修正案を提示する（`revise`）を選べます。Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読む場所に教訓が現れます。** `code.symbol` は定義に紐づくメモリを、それぞれのリンク状態とともに `lessons` として返します。`memory.for_file` はファイル内のコードに紐づくメモリを返します。Claude Code プラグインは、そのファイルを編集する前にそれらを表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **ブランチは自身のコードを読みます。** 古いテキストがまだ残っているリリースブランチでは、trunk が変更された後でも、教訓は `fresh` と表示されます。
- **コードと並ぶドキュメント。** リポジトリの trunk コード名前空間に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索でコードとともに読み取れます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** `namespace.validate` は、コードが変更された、または失われたメモリを一覧表示し、ダッシュボードは各メモリのリンクとその状態を表示します。

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。プラグインまたは MCP でコーディングエージェントを接続し（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）、バッククォートで定義を名指しした教訓を保存します。trunk がインデックスされ、次のリンク処理（デフォルトでは 60 秒ごと）が実行されると、その定義に対する `code.symbol` は、その教訓を `lessons` の下に返します。
