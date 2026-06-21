# Telegram Bot API — Rich Messages

Complete reference for Rich Messages (structured formatting) from Telegram Bot
API **10.1** (June 11, 2026). Source: <https://core.telegram.org/bots/api#rich-messages>.

Rich Messages are extended structured formatting: headings, lists, tables, media,
quotations, collapsible blocks, footnotes, formulas. Telegram clients render them
accordingly. Content is supplied in **Markdown** or **HTML** style.

Plain URLs, e-mails, @username mentions, hashtags, cashtags, bot commands, phone
numbers, and bank card numbers are auto-detected. Disable auto-detection with
`skip_entity_detection: True`. Before opening an inline link, the client shows an
alert ("Open this link?" + the full URL).

---

## Limits

| Limit | Value |
|---|---|
| UTF-8 characters in text | up to **32768** (including custom emoji alt-text and formula source) |
| Blocks (incl. nested) | up to **500** (nested blocks, list items, ordered list items, table rows, quotation blocks, details blocks) |
| Formatting/block nesting levels | up to **16** |
| Total media attachments | up to **50** (photo, video, audio) |
| Table columns | up to **20** |

---

## Methods

### `sendRichMessage`

Sends a rich message. If the message contains a media block, the bot must have
the right to send media to the chat. On success returns the sent `Message`.

| Parameter | Type | Required | Description |
|---|---|---|---|
| business_connection_id | String | Optional | Identifier of the business connection on whose behalf the message is sent. A bot can send rich messages from a business account only if the corresponding user can send them. |
| chat_id | Integer or String | Yes | ID of the target chat or username (`@username`) of the target bot, supergroup, or channel |
| message_thread_id | Integer | Optional | ID of the forum thread (topic); only for forum supergroups and private chats of bots with forum topic mode enabled |
| direct_messages_topic_id | Integer | Optional | ID of the direct messages topic; required if the message is sent to a direct messages chat |
| rich_message | InputRichMessage | Yes | The message to send |
| disable_notification | Boolean | Optional | Silent delivery — notification without sound |
| protect_content | Boolean | Optional | Protects content from forwarding and saving |
| allow_paid_broadcast | Boolean | Optional | Up to 1000 messages/sec bypassing broadcast limits for 0.1 Telegram Stars/message (charged to the bot's balance) |
| message_effect_id | String | Optional | ID of the message effect; private chats only |
| suggested_post_parameters | SuggestedPostParameters | Optional | JSON object of suggested post parameters; direct messages chats only |
| reply_parameters | ReplyParameters | Optional | Description of the message to reply to |
| reply_markup | InlineKeyboardMarkup or ReplyKeyboardMarkup or ReplyKeyboardRemove or ForceReply | Optional | Additional interface options (inline keyboard, etc.) |

### `sendRichMessageDraft`

Streams a partial rich message to the user while it is being generated. The
streamed draft is **ephemeral** — it acts as a temporary 30-second preview. After
finalization you **must** call `sendRichMessage` with the full message to persist
it in the chat. Returns `True` on success.

| Parameter | Type | Required | Description |
|---|---|---|---|
| chat_id | Integer | Yes | ID of the target private chat |
| message_thread_id | Integer | Optional | Thread ID |
| draft_id | Integer | Yes | Draft ID; must be non-zero. Changes to drafts with the same ID are animated. |
| rich_message | InputRichMessage | Yes | The partial message being streamed |

> The `RichBlockThinking` block / `<tg-thinking>` tag is available only in
> `sendRichMessageDraft`.

### `editMessageText` (`rich_message`)

`editMessageText` gained a `rich_message` parameter for editing rich messages
(analogous to `InputRichMessage`).

---

## Objects

### `RichMessage`

A rich-formatted message (what arrives in a `Message`).

| Field | Type | Description |
|---|---|---|
| blocks | Array of RichBlock | Message content |
| is_rtl | Boolean | Optional. True if the message should be displayed right-to-left |

### `InputRichMessage`

Describes a rich message to send. **Exactly one** of the `html` or `markdown`
fields must be set.

| Field | Type | Description |
|---|---|---|
| html | String | Optional. Content in HTML formatting |
| markdown | String | Optional. Content in Markdown formatting |
| is_rtl | Boolean | Optional. True — display right-to-left |
| skip_entity_detection | Boolean | Optional. True — skip auto-detection of entities (URLs, e-mails, @mentions, hashtags, cashtags, bot commands, phone numbers) |

### `InputRichMessageContent`

Content of a rich message to be sent as the result of an inline query (used as
`InputMessageContent` in inline, guest, and Web App queries).

| Field | Type | Description |
|---|---|---|
| rich_message | InputRichMessage | The message to send |

---

## Rich Markdown style

Compatible with GitHub Flavored Markdown where possible and may contain arbitrary
HTML. Passed in the `markdown` field.

### Inline formatting

```text
**bold text**
__bold text__
*italic text*
_italic text_
~~strikethrough text~~
`inline fixed-width code`
==marked text==
||spoiler||

[inline URL](https://t.me/)
[inline e-mail](mailto:user@example.com)
[inline phone number](tel:+123456789)
[inline mention of a user](tg://user?id=123456789)
![](tg://emoji?id=5368324170671202286)
![22:45 tomorrow](tg://time?unix=1647531900&format=wDT)
$x^2 + y^2$
\#hashtag $USD +12345678901, card: 4242 4242 4242 4242, https://t.me t.me a@t.me /command @username
```

### Block constructs

````text
# Heading 1
## Heading 2
### Heading 3
#### Heading 4
##### Heading 5
###### Heading 6

Paragraph text

```python
  print('pre-formatted fixed-width code block written in the Python programming language')
```

---

- unordered list item
* unordered list item
+ unordered list item

1. ordered list item
2. ordered list item

- [ ] task list item
- [x] completed task list item

>Block quotation started
>
>Block quotation continued on the next line
>Block quotation continued on the same line
>
>The last line of the block quotation
````

### Media (standalone block only)

```text
![](https://telegram.org/example/photo.jpg)
![](https://telegram.org/example/video.mp4)
![](https://telegram.org/example/audio.mp3)
![](https://telegram.org/example/audio.ogg)
![](https://telegram.org/example/animation.gif)

![](https://telegram.org/example/photo.jpg "Photo caption")
![](https://telegram.org/example/video.mp4 "Video caption")
![](https://telegram.org/example/audio.mp3 "Audio caption")
![](https://telegram.org/example/audio.ogg "Voice note caption")
![](https://telegram.org/example/animation.gif "Animation caption")
```

### Tables, footnotes, formulas

````text
| Header 1 | Header 2 |
|:---------|:--------:|
| left     | center   |

Text with a reference[^id1] and another one[^id2].

[^id1]: Definition of the first footnote.
[^id2]: Definition of the second footnote.

$$E = mc^2$$

```math
E = mc^2
```
````

### HTML tags inside Markdown

HTML tags are used for features without a Markdown syntax:

```text
<u>underlined text</u>, <ins>underlined text</ins>
<sub>subscript text</sub>
<sup>superscript text</sup>
<a name="chapter-1"></a>
<aside>Pull quote<cite>The Author</cite></aside>
<details open><summary>Title</summary>Content</details>
<tg-map lat="41.9" long="12.5" zoom="14"/>
<tg-collage><img src="https://telegram.org/example/photo.jpg"/><figcaption>Caption<cite>The Author</cite></figcaption></tg-collage>
<tg-slideshow><img src="https://telegram.org/example/photo.jpg"/><video src="https://telegram.org/example/video.mp4"/><figcaption>Slideshow caption<cite>The Author</cite></figcaption></tg-slideshow>
```

Additionally available in `sendRichMessageDraft`:

```text
<tg-thinking>Thinking...</tg-thinking>
```

**Notes (Markdown):**

- Rich Markdown is compatible with GFM where possible and may contain arbitrary HTML.
- Media is specified only as a standalone block. Media blocks support HTTP/HTTPS URLs only.
- Media type is determined by MIME type and URL.
- In the media syntax, the optional title after the URL is used as the caption.
- Table cells contain inline formatting only.
- Formula source is treated as raw LaTeX.
- Markdown is **not** parsed inside block-level HTML tags, except `<details>`, `<tg-collage>`, and `<tg-slideshow>` — those use HTML tags only.
- See the [Date-time formatting](#date-time-formatting) section.

---

## Rich HTML style

Passed in the `html` field. Supported tags:

```text
<a name="chapter-0"></a>
<b>bold text</b>, <strong>bold text</strong>
<i>italic text</i>, <em>italic text</em>
<u>underlined text</u>, <ins>underlined text</ins>
<s>strikethrough text</s>, <strike>strikethrough text</strike>, <del>strikethrough text</del>
<code>inline fixed-width code</code>
<mark>marked text</mark>
<sub>subscript text</sub>
<sup>superscript text</sup>
<tg-spoiler>spoiler</tg-spoiler>

<a href="#note-1">Reference</a>
<a href="https://t.me/">inline URL</a>
<a href="mailto:user@example.com">inline e-mail</a>
<a href="tel:+123456789">inline phone number</a>
<a href="tg://user?id=123456789">inline mention of a user</a>
<a href="#chapter-1">in-document link</a>
<a name="chapter-1"></a>

<tg-reference name="note-1">Referenced text</tg-reference>
<tg-emoji emoji-id="5368324170671202286"></tg-emoji>
<img src="tg://emoji?id=5368324170671202286" alt=""/>
<tg-time unix="1647531900" format="wDT">22:45 tomorrow</tg-time>
<tg-math>x^2 + y^2</tg-math>

<h1>Heading 1</h1> ... <h6>Heading 6</h6>

<p>Paragraph text</p>
<pre>pre-formatted fixed-width code block</pre>
<pre><code class="language-python">  print('...')</code></pre>
<footer>Footer text</footer>
<hr/>
<ul><li>unordered list item</li></ul>
<ol><li>ordered list item</li></ol>
<ol start="3" type="a" reversed><li>ordered list item</li></ol>
<ol><li value="7" type="i">ordered list item with explicit number</li></ol>
<ul>
<li><input type="checkbox" checked>Checked checkbox</li>
<li><input type="checkbox">Unchecked checkbox</li>
</ul>

<blockquote>Block quotation started<br>...<cite>The Author</cite></blockquote>
<aside>Pull quote<cite>The Author</cite></aside>

<img src="https://telegram.org/example/photo.jpg"/>
<video src="https://telegram.org/example/video.mp4"></video>
<audio src="https://telegram.org/example/audio.mp3"></audio>
<video src="https://telegram.org/example/animation.gif"></video>

<figure><img src="..." tg-spoiler/><figcaption>Photo caption<cite>Photo credit</cite></figcaption></figure>
<figure><video src="..." tg-spoiler></video><figcaption>Video caption</figcaption></figure>
<figure><audio src="..."></audio><figcaption>Audio caption</figcaption></figure>

<tg-map lat="41.9" long="12.5" zoom="14"/>
<figure><tg-map lat="41.9" long="12.5" zoom="14"/><figcaption>Map caption</figcaption></figure>

<tg-collage><img src="..."/><video src="..."/></tg-collage>
<tg-slideshow><img src="..."/><video src="..."/><figcaption>Slideshow caption</figcaption></tg-slideshow>

<table><tr><th>Header 1</th><th>Header 2</th></tr><tr><td>Value 1</td><td>Value 2</td></tr></table>
<table bordered striped><caption>Table caption</caption>
<tr><td colspan="2" rowspan="2" align="left">Value</td><td align="center">Value2</td><td align="right">Value3</td></tr>
<tr><td valign="top">Value4</td><td valign="middle">Value5</td><td valign="bottom">Value6</td></tr></table>

<details><summary>Title</summary>Content</details>
<details open><summary>Title</summary>Content</details>
<tg-math-block>E = mc^2</tg-math-block>
```

Additionally available in `sendRichMessageDraft`:

```text
<tg-thinking>Thinking...</tg-thinking>
```

**Notes (HTML):**

- Only the tags listed above are supported.
- All numeric HTML entities are supported.
- Of the named HTML entities, only these are supported: `&lt;`, `&gt;`, `&amp;`, `&quot;`, `&apos;`, `&nbsp;`, `&hellip;`, `&mdash;`, `&ndash;`, `&lsquo;`, `&rsquo;`, `&ldquo;`, `&rdquo;`.
- The programming language for a pre block is set via nested `<pre>` and `<code>`. A standalone `<code>` cannot specify a language.
- `mailto:...`, `tel:...`, and `tg://user?id=...` links render as e-mail, phone, and inline-mention links. All others render as regular inline links.
- Images, videos, and audio are specified only as standalone media blocks. HTTP/HTTPS URLs only.
- An empty `<a name="..."></a>` creates an anchor referenced via `<a href="#...">...</a>`.
- Inside `<figcaption>`, the `<cite>` tag sets the caption credit.
- `<tg-reference name="...">...</tg-reference>` defines referenced text linked via `<a href="#...">...</a>`.
- A `<details>` body may contain rich content. With the `open` attribute the block is expanded by default.
- Formula source is raw LaTeX.

---

## RichText (inline formatting)

`RichText` may be a `String` (plain text), an `Array of RichText`, or one of the
types below. Each has a `type` discriminator.

| Object type | `type` | Extra fields (besides `type`, `text: RichText`) |
|---|---|---|
| RichTextBold | `bold` | — |
| RichTextItalic | `italic` | — |
| RichTextUnderline | `underline` | — |
| RichTextStrikethrough | `strikethrough` | — |
| RichTextSpoiler | `spoiler` | — |
| RichTextSubscript | `subscript` | — |
| RichTextSuperscript | `superscript` | — |
| RichTextMarked | `marked` | — |
| RichTextCode | `code` | — (monowidth) |
| RichTextDateTime | `date_time` | `unix_time: Integer`, `date_time_format: String` |
| RichTextTextMention | `text_mention` | `user: User` |
| RichTextCustomEmoji | `custom_emoji` | `custom_emoji_id: String`, `alternative_text: String` (no `text`) |
| RichTextMathematicalExpression | `mathematical_expression` | `expression: String` (LaTeX; no `text`) |
| RichTextUrl | `url` | `url: String` |
| RichTextEmailAddress | `email_address` | `email_address: String` |
| RichTextPhoneNumber | `phone_number` | `phone_number: String` |
| RichTextBankCardNumber | `bank_card_number` | `bank_card_number: String` |
| RichTextMention | `mention` | `username: String` |
| RichTextHashtag | `hashtag` | `hashtag: String` |
| RichTextCashtag | `cashtag` | `cashtag: String` |
| RichTextBotCommand | `bot_command` | `bot_command: String` |
| RichTextAnchor | `anchor` | `name: String` (no `text`) |
| RichTextAnchorLink | `anchor_link` | `anchor_name: String` (empty name → start of message) |
| RichTextReference | `reference` | `name: String` |
| RichTextReferenceLink | `reference_link` | `reference_name: String` |

Detailed tables for the non-obvious types:

**RichTextDateTime** (`date_time`): `text` — RichText; `unix_time` — Integer (Unix
time of the entity); `date_time_format` — String (see [Date-time formatting](#date-time-formatting)).

**RichTextCustomEmoji** (`custom_emoji`): `custom_emoji_id` — String (get info via
`getCustomEmojiStickers`); `alternative_text` — String (fallback emoji).

**RichTextAnchorLink** (`anchor_link`): `text` — RichText (link text); `anchor_name`
— String. If the name is empty, the link points to the start of the message.

---

## RichBlock (blocks)

`RichBlock` is one of the types below, with a `type` discriminator.

### Text blocks

| Object | `type` | Fields |
|---|---|---|
| RichBlockParagraph | `paragraph` | `text: RichText`. HTML `<p>` |
| RichBlockSectionHeading | `heading` | `text: RichText`; `size: Integer` 1–6 (1 is largest). HTML `<h1>`–`<h6>` |
| RichBlockPreformatted | `pre` | `text: RichText`; `language: String` (Optional). HTML `<pre><code>` |
| RichBlockFooter | `footer` | `text: RichText`. HTML `<footer>` |
| RichBlockDivider | `divider` | `type` only. HTML `<hr/>` |
| RichBlockMathematicalExpression | `mathematical_expression` | `expression: String` (LaTeX). HTML `<tg-math-block>` |
| RichBlockAnchor | `anchor` | `name: String`. HTML `<a name="...">` |

### Lists and quotations

**RichBlockList** (`list`) — HTML `<ul>`/`<ol>` with `<li>`:

| Field | Type | Description |
|---|---|---|
| type | String | `list` |
| items | Array of RichBlockListItem | List items |

**RichBlockBlockQuotation** (`blockquote`) — HTML `<blockquote>`:

| Field | Type | Description |
|---|---|---|
| type | String | `blockquote` |
| blocks | Array of RichBlock | Block content |
| credit | RichText | Optional. Block credit |

**RichBlockPullQuotation** (`pullquote`) — centered quote, ~ HTML `<aside>`:

| Field | Type | Description |
|---|---|---|
| type | String | `pullquote` |
| text | RichText | Block text |
| credit | RichText | Optional. Block credit |

### Media containers

**RichBlockCollage** (`collage`) — HTML `<tg-collage>`:

| Field | Type | Description |
|---|---|---|
| type | String | `collage` |
| blocks | Array of RichBlock | Collage items |
| caption | RichBlockCaption | Optional. Caption |

**RichBlockSlideshow** (`slideshow`) — HTML `<tg-slideshow>`: same fields as the
collage, with `type` = `slideshow`.

### Table

**RichBlockTable** (`table`) — HTML `<table>`:

| Field | Type | Description |
|---|---|---|
| type | String | `table` |
| cells | Array of Array of RichBlockTableCell | Table cells |
| is_bordered | True | Optional. True if the table is bordered |
| is_striped | True | Optional. True if the table is striped |
| caption | RichText | Optional. Table caption |

### Collapsible block

**RichBlockDetails** (`details`) — HTML `<details>`:

| Field | Type | Description |
|---|---|---|
| type | String | `details` |
| summary | RichText | Always-visible block summary |
| blocks | Array of RichBlock | Block content |
| is_open | True | Optional. True if the content is visible by default |

### Map

**RichBlockMap** (`map`) — HTML `<tg-map>`:

| Field | Type | Description |
|---|---|---|
| type | String | `map` |
| location | Location | Location of the map center |
| zoom | Integer | Map zoom; 13–20 |
| width | Integer | Expected map width |
| height | Integer | Expected map height |
| caption | RichBlockCaption | Optional. Caption |

### Media blocks

All have `caption: RichBlockCaption` (Optional).

| Object | `type` | Media fields | Spoiler |
|---|---|---|---|
| RichBlockAnimation | `animation` | `animation: Animation` | `has_spoiler: True` (Optional). HTML `<video>` |
| RichBlockAudio | `audio` | `audio: Audio` | — HTML `<audio>` |
| RichBlockPhoto | `photo` | `photo: Array of PhotoSize` | `has_spoiler: True` (Optional). HTML `<img>` |
| RichBlockVideo | `video` | `video: Video` | `has_spoiler: True` (Optional). HTML `<video>` |
| RichBlockVoiceNote | `voice_note` | `voice_note: Voice` | — HTML `<audio>` |

### `RichBlockThinking` (draft only)

**RichBlockThinking** (`thinking`) — a "Thinking…" placeholder, HTML `<tg-thinking>`.
Used **only** in `sendRichMessageDraft`; it does not appear in received messages.

| Field | Type | Description |
|---|---|---|
| type | String | `thinking` |
| text | RichText | Block text. Custom emoji examples (recommended): <https://t.me/addemoji/AIActions> |

---

## Helper objects

### `RichBlockCaption`

Caption of a rich block.

| Field | Type | Description |
|---|---|---|
| text | RichText | Block caption |
| credit | RichText | Optional. Block credit (HTML `<cite>`) |

### `RichBlockTableCell`

A table cell.

| Field | Type | Description |
|---|---|---|
| text | RichText | Optional. Cell text. If empty, the cell is invisible. |
| is_header | True | Optional. True if a header cell |
| colspan | Integer | Optional. Number of columns if > 1 |
| rowspan | Integer | Optional. Number of rows if > 1 |
| align | String | Horizontal alignment: `left`, `center`, `right` |
| valign | String | Vertical alignment: `top`, `middle`, `bottom` |

### `RichBlockListItem`

A list item.

| Field | Type | Description |
|---|---|---|
| label | String | Item label |
| blocks | Array of RichBlock | Item content |
| has_checkbox | True | Optional. True if a checkbox is present |
| is_checked | True | Optional. True if the checkbox is checked |
| value | Integer | Optional. For ordered lists — numeric value of the label |
| type | String | Optional. For ordered lists — label type: `a` (lowercase letters), `A` (uppercase), `i` (lowercase Roman), `I` (uppercase Roman), `1` (decimal) |

---

## Date-time formatting

The format is given by a string matching the regex `r|w?[dD]?[tT]?`.

If the string is empty, the text is shown as-is, but the user still receives the
date in their local format. Control characters:

| Character | Effect |
|---|---|
| `r` | Time relative to now. **Cannot** be combined with other characters. |
| `w` | Day of the week in the user's language |
| `d` | Date in short form (e.g. "17.03.22") |
| `D` | Date in long form (e.g. "March 17, 2022") |
| `t` | Time in short form (e.g. "22:45") |
| `T` | Time in long form (e.g. "22:45:00") |

Examples (Markdown / `tg://time`):

```text
![22:45 tomorrow](tg://time?unix=1647531900&format=wDT)
![22:45 tomorrow](tg://time?unix=1647531900&format=t)
![22:45 tomorrow](tg://time?unix=1647531900&format=r)
![22:45 tomorrow](tg://time?unix=1647531900)
```

HTML equivalent: `<tg-time unix="1647531900" format="wDT">22:45 tomorrow</tg-time>`.

---

## Usage in agent-bot

The project sends rich messages via aiogram (`>=3.29`, Bot API 10.1 support).

- [src/ui/markdown.py](../src/ui/markdown.py) — `to_html()` converts the agent's
  Markdown into a subset of Rich HTML (`h1`–`h6`, `pre/code`, `blockquote`, `table`,
  `hr`, whitelisted `mark/sub/sup/details/summary/tg-spoiler`).
- `send_md_to_chat()` sends via `bot.send_rich_message(rich_message=InputRichMessage(html=chunk))`,
  with a plain-text fallback on `TelegramBadRequest`. Chunk ≤ `TG_LIMIT` (4000).
- [src/infra/streaming.py](../src/infra/streaming.py) — `DraftStreamer` uses
  `sendRichMessageDraft` + `<tg-thinking>` for thinking tokens; `ToolStatusMirror`
  works via `sendRichMessage` / `edit_message_text(rich_message=)`.

> The Bot API text limit is 32768 characters; the project chunks at 4000 for
> compatibility/partial delivery.
