export function AboutPage() {
  return (
    <section className="about-page">
      <header className="about-header">
        <p className="hero-kicker">How to Use This App</p>
        <h1>Simple workflow for sales reps</h1>
      </header>

      <div className="about-grid">
        <article className="about-card">
          <h3>1. Ask a targeting question</h3>
          <p>Example: which providers should move from Lite to Comprehensive this quarter.</p>
        </article>
        <article className="about-card">
          <h3>2. Read the recommendation first</h3>
          <p>Use the top recommendation block as your immediate action summary for account planning.</p>
        </article>
        <article className="about-card">
          <h3>3. Use visuals in rep prep</h3>
          <p>KPI cards and charts show impact quickly for manager reviews and territory planning calls.</p>
        </article>
        <article className="about-card">
          <h3>4. Execute talk tracks</h3>
          <p>Combine MAS recommendation and source context to draft email, call script, and cadence in one pass.</p>
        </article>
      </div>
    </section>
  );
}
