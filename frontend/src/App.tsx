const EXAMPLE_QUERIES = [
  "Find structuring patterns in the last 30 days",
  "Which customers made 10+ transactions under $10,000?",
  "Is customer ID 4521 suspicious?",
];

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-hairline bg-surface px-6 py-3">
        <h1 className="text-[15px] font-semibold tracking-tight">Caseline</h1>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-10">
        <input
          type="text"
          placeholder="Ask about suspicious activity… (press / to focus)"
          className="w-full rounded border border-hairline bg-surface px-4 py-3 text-[14px] outline-none focus:border-accent"
        />
        <div className="mt-3 flex gap-2">
          {EXAMPLE_QUERIES.map((q) => (
            <button
              key={q}
              className="rounded border border-hairline bg-surface px-3 py-1.5 text-[12.5px] text-muted hover:border-accent hover:text-accent"
            >
              {q}
            </button>
          ))}
        </div>

        <p className="mt-16 text-center text-[13px] text-muted">
          Caseline plans each query, runs only the analysis tools it needs, and
          explains every flag. Try an example above.
        </p>
      </main>
    </div>
  );
}
