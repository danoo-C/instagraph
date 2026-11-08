/** Resize charts to the print width (ECharts listens to window resize), then print. */
export function printNow(): void {
  window.dispatchEvent(new Event("resize"));
  window.print();
}
