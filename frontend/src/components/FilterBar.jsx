export default function FilterBar({
  showDirectionToggle = true,
  direction,
  onDirectionChange,
  sector,
  onSectorChange,
  sectors,
  maxPrice,
  onMaxPriceChange,
  onRescan,
  scanning,
}) {
  return (
    <div className="filter-bar">
      {showDirectionToggle && (
        <div className="direction-toggle">
          <button
            className={direction === "losers" ? "active down" : ""}
            onClick={() => onDirectionChange("losers")}
          >
            נפלו קיצוני
          </button>
          <button
            className={direction === "gainers" ? "active up" : ""}
            onClick={() => onDirectionChange("gainers")}
          >
            עלו קיצוני
          </button>
        </div>
      )}

      <select value={sector} onChange={(e) => onSectorChange(e.target.value)}>
        <option value="">כל הסקטורים</option>
        {sectors.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <input
        type="number"
        placeholder="מחיר מקסימלי ($)"
        value={maxPrice}
        onChange={(e) => onMaxPriceChange(e.target.value)}
        min="0"
      />

      <button className="rescan-btn" onClick={onRescan} disabled={scanning}>
        {scanning ? "סורק..." : "סרוק עכשיו"}
      </button>
    </div>
  );
}
