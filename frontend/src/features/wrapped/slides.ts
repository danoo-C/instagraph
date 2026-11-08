import type { Schemas } from "@/features/people/apiTypes";
import { drilldownQuery } from "@/lib/drilldown";
import type { Slide, SlideBlock, SlideTheme } from "@/features/wrapped/model";
import { personColor } from "@/lib/colors";
import { formatDate, formatDuration, formatNumber, formatPercent } from "@/lib/format";

type Wrapped = Schemas["WrappedResponse"];

/** Story backgrounds (dark gradients keep white text at AA contrast). */
const THEMES: SlideTheme[] = [
  { from: "#312e81", to: "#7c3aed", fg: "#ffffff", muted: "#ddd6fe", accent: "#fbbf24" },
  { from: "#0f172a", to: "#1e40af", fg: "#ffffff", muted: "#bfdbfe", accent: "#38bdf8" },
  { from: "#831843", to: "#be123c", fg: "#ffffff", muted: "#fecdd3", accent: "#fde047" },
  { from: "#064e3b", to: "#0f766e", fg: "#ffffff", muted: "#a7f3d0", accent: "#fcd34d" },
  { from: "#7c2d12", to: "#c2410c", fg: "#ffffff", muted: "#fed7aa", accent: "#fef08a" },
  { from: "#1e1b4b", to: "#0f172a", fg: "#ffffff", muted: "#c7d2fe", accent: "#a78bfa" },
];

const TYPE_LABEL: Record<string, string> = {
  photo: "📷 Photo",
  video: "🎬 Video",
  audio: "🎙️ Voice message",
  gif: "GIF",
  sticker: "Sticker",
  share: "🔗 Shared post",
  call: "📞 Call",
  like: "❤️ Like",
};

export interface BuildOptions {
  anonymize?: boolean;
  /** "All chats" or the chat title. */
  scopeLabel?: string;
}

const MONTH_LETTERS = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { month: "narrow" }).format(new Date(`${iso}T12:00:00`));

/** Build the slideshow (§5.10) from a Wrapped response; slides without data are skipped. */
export function buildSlides(w: Wrapped, opts: BuildOptions = {}): Slide[] {
  const scope = opts.scopeLabel ?? w.chat_title ?? "All chats";
  const footer = `Instagraph Wrapped ${w.year} · ${scope}`;
  const slides: Slide[] = [];
  const add = (s: Omit<Slide, "theme" | "footer">) =>
    slides.push({ ...s, footer, theme: THEMES[slides.length % THEMES.length]! });

  add({
    key: "intro",
    kicker: "Instagraph Wrapped",
    title: `Your ${w.year} in messages`,
    blocks: [
      { kind: "big", text: String(w.year) },
      { kind: "text", text: scope, size: "lg" },
      { kind: "text", text: "Tap to start", size: "sm", muted: true },
    ],
  });

  if (w.total_messages === 0) {
    add({
      key: "empty",
      kicker: "Messages",
      title: "A quiet year",
      blocks: [{ kind: "text", text: `No messages in ${w.year} for this scope.` }],
    });
    return slides;
  }

  add({
    key: "total",
    kicker: "Messages",
    title: "You kept the conversation going",
    blocks: [
      { kind: "big", text: formatNumber(w.total_messages) },
      { kind: "text", text: `messages in ${w.year}`, size: "lg" },
      {
        kind: "text",
        text: `${formatNumber(w.sent_by_me)} sent · ${formatNumber(w.received)} received`,
        muted: true,
      },
      ...(w.months.length
        ? [
            {
              kind: "bars",
              values: w.months.map((m) => m.messages),
              labels: w.months.map((m) => MONTH_LETTERS(m.month)),
            } satisfies SlideBlock,
          ]
        : []),
    ],
    drilldown: drilldownQuery(w.total_drilldown),
  });

  if (w.top_people.length) {
    add({
      key: "people",
      kicker: "Your people",
      title: w.chat_id != null ? "Who talked the most" : "Your top 5",
      blocks: [
        {
          kind: "list",
          items: w.top_people.slice(0, 5).map((p, i) => ({
            rank: i + 1,
            label: p.person.name,
            value: formatNumber(p.messages),
            color: personColor(p.person.color_index),
          })),
        },
      ],
      drilldown: drilldownQuery(w.top_people[0]?.drilldown),
    });
  }

  if (w.top_emoji) {
    const others = w.top_emojis.filter((e) => e.emoji !== w.top_emoji?.emoji).slice(0, 4);
    add({
      key: "emoji",
      kicker: "Top emoji",
      title: "Your emoji of the year",
      blocks: [
        { kind: "big", text: w.top_emoji.emoji },
        { kind: "text", text: `used ${formatNumber(w.top_emoji.count)} times`, size: "lg" },
        ...(others.length
          ? [
              {
                kind: "chips",
                items: others.map((e) => `${e.emoji} ${formatNumber(e.count)}`),
              } satisfies SlideBlock,
            ]
          : []),
      ],
      drilldown: drilldownQuery(w.top_emoji.drilldown),
    });
  }

  if (w.busiest_day) {
    add({
      key: "busiest",
      kicker: "Busiest day",
      title: "The day nobody put the phone down",
      blocks: [
        { kind: "big", text: formatDate(w.busiest_day.date, { month: "short", day: "numeric" }) },
        {
          kind: "text",
          text: formatDate(w.busiest_day.date, { weekday: "long", year: "numeric" }),
          muted: true,
        },
        {
          kind: "text",
          text: `${formatNumber(w.busiest_day.messages)} messages in one day`,
          size: "lg",
        },
      ],
      drilldown: drilldownQuery(w.busiest_day.drilldown),
    });
  }

  const night = w.my_night_owl_share ?? w.night_owl_share;
  if (night != null) {
    add({
      key: "night",
      kicker: "Night owl",
      title: night >= 0.15 ? "Who needs sleep anyway? 🦉" : "Mostly a daytime texter ☀️",
      blocks: [
        { kind: "big", text: formatPercent(night, 0) },
        { kind: "text", text: "of your messages were sent between midnight and 5 am", size: "lg" },
        ...(w.night_owl_share != null && w.my_night_owl_share != null
          ? [
              {
                kind: "text",
                text: `Everyone: ${formatPercent(w.night_owl_share, 0)}`,
                muted: true,
              } satisfies SlideBlock,
            ]
          : []),
      ],
    });
  }

  if (w.longest_streak && w.longest_streak.days > 0) {
    const s = w.longest_streak;
    add({
      key: "streak",
      kicker: "Longest streak",
      title: "Day after day 🔥",
      blocks: [
        { kind: "big", text: `${formatNumber(s.days)} days` },
        { kind: "text", text: "in a row with messages", size: "lg" },
        { kind: "text", text: `${formatDate(s.start)} – ${formatDate(s.end)}`, muted: true },
      ],
      drilldown: {
        start: s.start,
        end: s.end,
        ...(w.chat_id != null ? { thread: w.chat_id } : {}),
      },
    });
  }

  if (w.top_word) {
    const others = w.top_words.filter((x) => x.word !== w.top_word).slice(0, 5);
    add({
      key: "word",
      kicker: "Top word",
      title: "The word you couldn't stop using",
      blocks: [
        { kind: "big", text: `“${w.top_word}”` },
        ...(w.top_words[0]?.word === w.top_word
          ? [
              {
                kind: "text",
                text: `${formatNumber(w.top_words[0].count)} times`,
                size: "lg",
              } satisfies SlideBlock,
            ]
          : []),
        ...(others.length
          ? [{ kind: "chips", items: others.map((x) => x.word) } satisfies SlideBlock]
          : []),
      ],
      drilldown: {
        q: w.top_word,
        mode: "word",
        start: `${w.year}-01-01`,
        end: `${w.year}-12-31`,
        ...(w.chat_id != null ? { thread: w.chat_id } : {}),
      },
    });
  } else if (opts.anonymize) {
    add({
      key: "word",
      kicker: "Top word",
      title: "The word you couldn't stop using",
      blocks: [
        { kind: "big", text: "🔒" },
        { kind: "text", text: "Hidden in anonymize mode", size: "lg" },
      ],
    });
  }

  if (w.fastest_replier) {
    const f = w.fastest_replier;
    add({
      key: "replier",
      kicker: "Fastest replier",
      title: "Always there in a flash ⚡",
      blocks: [
        {
          kind: "list",
          items: [{ rank: 1, label: f.person.name, color: personColor(f.person.color_index) }],
        },
        { kind: "big", text: formatDuration(f.median_ms / 1000) },
        {
          kind: "text",
          text: `median reply time · ${formatNumber(f.replies)} replies`,
          muted: true,
        },
      ],
    });
  }

  if (w.most_reacted_message) {
    const m = w.most_reacted_message;
    const text =
      m.message.text ??
      (opts.anonymize ? "Message text hidden" : (TYPE_LABEL[m.message.type] ?? m.message.type));
    add({
      key: "reacted",
      kicker: "Most reacted message",
      title: `${formatNumber(m.reactions)} reactions`,
      blocks: [
        {
          kind: "quote",
          text,
          meta: `${m.message.sender.name} · ${m.thread_title} · ${formatDate(m.message.ts)}`,
        },
        ...(m.emojis.length ? [{ kind: "chips", items: m.emojis } satisfies SlideBlock] : []),
      ],
      drilldown: drilldownQuery(m.drilldown),
    });
  }

  if (w.longest_call && w.longest_call.duration_ms > 0) {
    const c = w.longest_call;
    add({
      key: "call",
      kicker: "Longest call",
      title: "So much to talk about 📞",
      blocks: [
        { kind: "big", text: formatDuration(c.duration_ms / 1000) },
        { kind: "text", text: c.thread_title, size: "lg" },
        { kind: "text", text: formatDate(c.message.ts), muted: true },
      ],
      drilldown: drilldownQuery(c.drilldown),
    });
  }

  const awards = w.personality.awards;
  add({
    key: "personality",
    kicker: "Your personality",
    title: awards.length ? "You are…" : "Your personality",
    blocks: awards.length
      ? [
          {
            kind: "list",
            items: awards
              .slice(0, 7)
              .map((a) => ({ icon: a.emoji, label: a.title, value: a.rule })),
          },
          ...(awards.length > 7
            ? [
                {
                  kind: "text",
                  text: `+ ${awards.length - 7} more`,
                  muted: true,
                } satisfies SlideBlock,
              ]
            : []),
        ]
      : [
          {
            kind: "text",
            text: w.personality.summary || "Not enough activity to hand out awards.",
            size: "lg",
          },
        ],
  });

  add({
    key: "outro",
    kicker: "That's a wrap",
    title: `See you in ${w.year + 1}`,
    blocks: [
      { kind: "big", text: "👋" },
      { kind: "text", text: `${formatNumber(w.total_messages)} messages · ${scope}`, muted: true },
    ],
  });
  return slides;
}

/** Plain-text version of a slide (screen readers, alt text). */
export function slideAltText(s: Slide): string {
  const parts = [s.kicker, s.title];
  for (const b of s.blocks) {
    if (b.kind === "big" || b.kind === "text") parts.push(b.text);
    else if (b.kind === "list")
      parts.push(
        b.items.map((i) => [i.icon, i.rank, i.label, i.value].filter(Boolean).join(" ")).join("; "),
      );
    else if (b.kind === "chips") parts.push(b.items.join(", "));
    else if (b.kind === "quote") parts.push(`${b.text} — ${b.meta}`);
  }
  return parts.join(". ");
}
