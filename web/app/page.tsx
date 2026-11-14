import { MetricChart } from "@/components/metric-charts";
import { loadDashboard } from "@/lib/data";
import { NULL_TEXT, amount, count, exclusions, interval, percent, seconds } from "@/lib/format";

/** A missing value is the word null on muted ink, never a zero. */
function Cell({ text }: { text: string }) {
  return text === NULL_TEXT ? <span className="isnull">{text}</span> : <>{text}</>;
}

/** The chart key lives in HTML, not on the canvas: legible without JavaScript. */
function Legend({ items }: { items: { tone: string; label: string }[] }) {
  return (
    <ul className="legend">
      {items.map((item) => (
        <li key={item.label}>
          <span className={`swatch ${item.tone}`} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export default function Page() {
  const { title, basis, generated_at, agents } = loadDashboard();

  return (
    <main>
      <header>
        <h1>{title}</h1>
        <p>
          Frozen corpus, one row per agent, pairs as the denominator. Snapshots generated{" "}
          <strong>{generated_at}</strong>. Cost basis: <strong>{basis}</strong>.
        </p>
      </header>

      <h2>Acceptance and success</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th className="num">Acceptance</th>
              <th className="num">95% CI</th>
              <th className="num">Regression-free</th>
              <th className="num">Task success</th>
              <th className="num">Accepted / reviewed</th>
              <th className="num">Tokens per fix</th>
              <th className="num">Cost per fix</th>
              <th className="num">Median repair</th>
              <th className="num">False completion</th>
            </tr>
          </thead>
          <tbody>
            {agents.map(({ agent, metrics, denominators }) => (
              <tr key={agent}>
                <td className="agent">{agent}</td>
                <td className="num">
                  <Cell text={percent(metrics.human_acceptance_rate)} />
                </td>
                <td className="num">
                  <Cell text={interval(metrics.human_acceptance_ci)} />
                </td>
                <td className="num">
                  <Cell text={percent(metrics.regression_free_acceptance_rate)} />
                </td>
                <td className="num">
                  <Cell text={percent(metrics.task_success_rate)} />
                </td>
                <td className="num">
                  {count(denominators.accepted)} / {count(denominators.reviewed_pairs)}
                </td>
                <td className="num">
                  <Cell text={amount(metrics.tokens_per_accepted_fix)} />
                </td>
                <td className="num">
                  <Cell text={amount(metrics.cost_per_accepted_fix, 4)} />
                </td>
                <td className="num">
                  <Cell text={seconds(metrics.median_time_to_repair_s)} />
                </td>
                <td className="num">
                  <Cell text={percent(metrics.false_completion_rate)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Charts</h2>
      <div className="charts">
        <figure>
          <figcaption>Human acceptance with its 95% Wilson interval</figcaption>
          <div className="plot">
            <MetricChart
              kind="acceptance"
              agents={agents}
              label="Human acceptance per agent with its 95% Wilson interval"
            />
          </div>
          <Legend
            items={[
              { tone: "band", label: "95% interval" },
              { tone: "good", label: "acceptance" },
            ]}
          />
          <ul className="values">
            {agents.map((agent) => (
              <li key={agent.agent}>
                <b>{agent.agent}</b> {percent(agent.metrics.human_acceptance_rate)}
                {agent.metrics.human_acceptance_ci === null
                  ? null
                  : ` (${interval(agent.metrics.human_acceptance_ci)})`}
              </li>
            ))}
          </ul>
        </figure>

        <figure>
          <figcaption>Regression-free acceptance</figcaption>
          <div className="plot">
            <MetricChart
              kind="regression_free"
              agents={agents}
              label="Regression-free acceptance per agent"
            />
          </div>
          <ul className="values">
            {agents.map((agent) => (
              <li key={agent.agent}>
                <b>{agent.agent}</b> {percent(agent.metrics.regression_free_acceptance_rate)}
              </li>
            ))}
          </ul>
        </figure>

        <figure>
          <figcaption>Cost per accepted fix ({basis})</figcaption>
          <div className="plot">
            <MetricChart
              kind="cost"
              agents={agents}
              label="Cost per accepted fix per agent in USD"
            />
          </div>
          <ul className="values">
            {agents.map((agent) => (
              <li key={agent.agent}>
                <b>{agent.agent}</b> {amount(agent.metrics.cost_per_accepted_fix, 4)}
              </li>
            ))}
          </ul>
        </figure>

        <figure>
          <figcaption>Where the corpus ends up</figcaption>
          <div className="plot">
            <MetricChart
              kind="corpus"
              agents={agents}
              label="Share of pairs per agent: regression-free, accepted with a regression, not accepted"
            />
          </div>
          <Legend
            items={[
              { tone: "good", label: "accepted, regression-free" },
              { tone: "caution", label: "accepted, regression" },
              { tone: "bad", label: "not accepted" },
            ]}
          />
          <ul className="values">
            {agents.map((agent) => (
              <li key={agent.agent}>
                <b>{agent.agent}</b> {percent(agent.split.regression_free)} clean ·{" "}
                {percent(agent.split.with_regression)} regressed ·{" "}
                {percent(agent.split.not_accepted)} not accepted
              </li>
            ))}
          </ul>
        </figure>
      </div>

      <h2>Denominators and exclusions</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th className="num">Pairs</th>
              <th className="num">Reviewed</th>
              <th className="num">Accepted</th>
              <th className="num">Regression-free</th>
              <th className="num">Automated pass</th>
              <th className="num">No patch</th>
              <th className="num">Missing held-out</th>
              <th>Excluded</th>
              <th>Pricing version</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.agent}>
                <td className="agent">{agent.agent}</td>
                <td className="num">{count(agent.denominators.pairs)}</td>
                <td className="num">{count(agent.denominators.reviewed_pairs)}</td>
                <td className="num">{count(agent.denominators.accepted)}</td>
                <td className="num">{count(agent.denominators.regression_free_accepted)}</td>
                <td className="num">{count(agent.denominators.automated_pass_pairs)}</td>
                <td className="num">{count(agent.denominators.no_patch_pairs)}</td>
                <td className="num">{count(agent.denominators.missing_heldout_pairs)}</td>
                <td>{exclusions(agent.exclusions)}</td>
                <td>
                  <code>{agent.pricing_version}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="notes">
        <ul>
          <li>
            Denominators are pairs (task, agent), not attempts: an agent that retries must not look
            better than one that does not.
          </li>
          <li>
            <code>null</code> means the denominator was zero: a zero denominator is not zero cost or
            zero acceptance. Acceptance counts reviewed pairs only; a pair no reviewer has seen is
            unknown, not rejected.
          </li>
          <li>
            A missing held-out result is a data defect, not a pass: those pairs leave the
            regression-free rate and are counted above.
          </li>
          <li>
            Costs computed under different <code>pricing_version</code> values are not comparable.
          </li>
        </ul>
      </div>
    </main>
  );
}
