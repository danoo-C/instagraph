import { HeadlineTiles } from "@/pages/Overview/HeadlineTiles";
import { ContactsCard, GraphCard, TopOverTimeCard } from "@/pages/Overview/charts/ContactCharts";
import {
  AwardsSection,
  EmojisCard,
  SizeDistributionCard,
  YearsCard,
} from "@/pages/Overview/charts/MiscCharts";
import {
  CalendarCard,
  HoursCard,
  PunchCardCard,
  WeekdaysCard,
} from "@/pages/Overview/charts/PatternCharts";
import {
  ActiveChatsCard,
  CumulativeCard,
  NewConversationsCard,
  ReplyTimesCard,
  SentReceivedCard,
  TimelineCard,
  TypesCard,
} from "@/pages/Overview/charts/TimeCharts";

/** Overview of all chats (§5.3, §7.1). Every card fetches on its own. */
export default function OverviewPage() {
  return (
    <div className="page flex flex-col gap-6">
      <h1 className="page-title">Overview</h1>
      <HeadlineTiles />
      <div className="card-grid">
        <TimelineCard />
        <SentReceivedCard />
        <ContactsCard />
        <TopOverTimeCard />
        <CalendarCard />
        <PunchCardCard />
        <HoursCard />
        <WeekdaysCard />
        <CumulativeCard />
        <NewConversationsCard />
        <ActiveChatsCard />
        <TypesCard />
        <ReplyTimesCard />
        <EmojisCard />
        <SizeDistributionCard />
        <GraphCard />
        <YearsCard />
      </div>
      <AwardsSection />
    </div>
  );
}
