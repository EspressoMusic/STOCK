import { formatPrice, formatPercent } from "../format";
import { getHotSignal } from "../lib/hotSignal";

export default function StockRow({ stock, direction, onClick }) {
  const isBroken = direction === "broken";
  const changeClass = isBroken ? "chg-broken" : direction === "losers" ? "chg-down" : "chg-up";
  const badgeValue = isBroken ? stock.fifty_two_week_change_percent : stock.change_percent;
  const isHot = getHotSignal(stock).isHot;

  return (
    <button className={`stock-row${isHot ? " stock-row-hot" : ""}`} onClick={onClick}>
      <span className="stock-row-symbol">{stock.symbol}</span>
      <span className={`chg-badge ${changeClass}`}>{formatPercent(badgeValue)}</span>
      <span className="stock-row-price">{formatPrice(stock.price)}</span>
    </button>
  );
}
