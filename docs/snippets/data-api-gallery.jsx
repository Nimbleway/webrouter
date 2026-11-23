import { DATA_APIS } from "/snippets/data-apis-index.jsx";

// The Web Catalog: a search box, a category filter and one row per data API.
// Its data is generated from the vendor catalog by scripts/data_api_docs.py;
// nothing about a data API is written here.
export const DataApiGallery = () => {
  const { categories, vendors } = DATA_APIS;
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("category");
    if (wanted && categories.some((item) => item.id === wanted)) setCategory(wanted);
  }, []);

  const choose = (id) => {
    setCategory(id);
    const url = new URL(window.location.href);
    if (id === "all") url.searchParams.delete("category");
    else url.searchParams.set("category", id);
    window.history.replaceState(null, "", url);
  };

  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = vendors.filter((vendor) => {
    if (category !== "all" && vendor.category !== category) return false;
    const text = `${vendor.name} ${vendor.summary} ${vendor.terms}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });

  return (
    <div className="dag">
      <div className="dag-controls">
        <input
          className="dag-search"
          id="data-api-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search by name, site or job: emails, youtube.com, weather"
          aria-label="Search the Web Catalog"
        />
        <select
          className="dag-filter"
          id="data-api-category"
          value={category}
          onChange={(event) => choose(event.target.value)}
          aria-label="Category"
        >
          <option value="all">All categories ({vendors.length})</option>
          {categories.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} ({item.count})
            </option>
          ))}
        </select>
      </div>
      <p className="dag-status" aria-live="polite">
        {shown.length === vendors.length ? `${vendors.length} data APIs` : `${shown.length} of ${vendors.length} data APIs`}
      </p>
      <div className="dag-rows">
        {shown.map((vendor) => (
          <a className="dag-row" key={vendor.id} href={`/data-apis/${vendor.id}`}>
            <span className="dag-name">
              <img className="dag-icon" src={vendor.icon} alt="" width="24" height="24" loading="lazy" />
              {vendor.name}
            </span>
            <span className="dag-summary">{vendor.summary}</span>
            <span className={vendor.needs_key || vendor.some_key ? "dag-key" : "dag-key dag-key-free"}>
              {vendor.needs_key ? "Needs key" : vendor.some_key ? "Key for some calls" : "No key"}
            </span>
          </a>
        ))}
      </div>
      {shown.length === 0 && (
        <p className="dag-empty">
          No data API matches. Try fewer words or another category, or{" "}
          <a href="/vendors#finding-a-data-api">find one from code</a>.
        </p>
      )}
    </div>
  );
};
