import { MessageBrowser } from "@/features/messages/MessageBrowser";

/** Search = the full Message Browser (§5.9); every drilldown in the app lands here. */
export default function SearchPage() {
  return (
    <div className="page">
      <h1 className="page-title">Messages</h1>
      <MessageBrowser />
    </div>
  );
}
