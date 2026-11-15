import { MetricChart } from "@/components/metric-charts";
import { loadDashboard } from "@/lib/data";
import { NULL_TEXT, amount, count, exclusions, interval, percent, seconds } from "@/lib/format";

/** A calibration rate always carries its denominator: a bare share hides the sample. */
function rateText(rate: { count: number; of: number; rate: number | null }): string {
  return `${count(rate.count)}/${count(rate.of)} (${percent(rate.rate)})`;
}

function thresholdWord(value: boolean | null): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "not measured";
}

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
              <th>Verdict source</th>
              <th className="num">Acceptance</th>
              <th className="num">95% CI</th>
              <th className="num">Regression-free</th>
              <th className="num">Task success</th>
              <th className="num">Accepted / decided</th>
              <th className="num">Tokens per fix</th>
              <th className="num">Cost per fix</th>
              <th className="num">Median repair</th>
              <th className="num">False completion</th>
            </tr>
          </thead>
          <tbody>
            {agents.map(({ agent, verdict_source, metrics, denominators }) => (
              <tr key={agent}>
                <td className="agent">{agent}</td>
                <td>
                  <code>{verdict_source}</code>
                </td>
                <td className="num">
                  <Cell text={percent(metrics.acceptance_rate)} />
                </td>
                <td className="num">
                  <Cell text={interval(metrics.acceptance_ci)} />
                </td>
                <td className="num">
                  <Cell text={percent(metrics.regression_free_acceptance_rate)} />
                </td>
                <td className="num">
                  <Cell text={percent(metrics.task_success_rate)} />
                </td>
                <td className="num">
                  {count(denominators.accepted)} / {count(denominators.decided_pairs)}
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
          <figcaption>Acceptance with its 95% Wilson interval</figcaption>
          <div className="plot">
            <MetricChart
              kind="acceptance"
              agents={agents}
              label="Acceptance per agent with its 95% Wilson interval"
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
                <b>{agent.agent}</b> {percent(agent.metrics.acceptance_rate)}
                {agent.metrics.acceptance_ci === null
                  ? null
                  : ` (${interval(agent.metrics.acceptance_ci)})`}
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
              <th className="num">Decided</th>
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
                <td className="num">{count(agent.denominators.decided_pairs)}</td>
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

      {agents.some((agent) => agent.calibration !== undefined) ? (
        <>
          <h2>Judge calibration</h2>
          <p>
            The reference label is the held-out suite, which no model produced. A pair the
            held-out suite could not answer has no label and is counted as unlabelled, never as a
            judge mistake.
          </p>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th className="num">Labelled / unlabelled</th>
                  <th className="num">False acceptance</th>
                  <th className="num">False rejection</th>
                  <th className="num">Inter-judge agreement</th>
                  <th className="num">Brier</th>
                  <th className="num">Escalation</th>
                  <th>Within thresholds</th>
                </tr>
              </thead>
              <tbody>
                {agents
                  .filter((agent) => agent.calibration !== undefined)
                  .map((agent) => {
                    const calibration = agent.calibration;
                    if (calibration === undefined) return null;
                    return (
                      <tr key={agent.agent}>
                        <td className="agent">{agent.agent}</td>
                        <td className="num">
                          {count(calibration.labeled_pairs)} / {count(calibration.unlabeled_pairs)}
                        </td>
                        <td className="num">{rateText(calibration.false_acceptance)}</td>
                        <td className="num">{rateText(calibration.false_rejection)}</td>
                        <td className="num">{rateText(calibration.agreement)}</td>
                        <td className="num">
                          <Cell text={amount(calibration.brier === null ? null : String(calibration.brier))} />
                        </td>
                        <td className="num">{rateText(calibration.escalation)}</td>
                        <td>{thresholdWord(calibration.meets_thresholds)}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <div className="notes">
        <ul>
          <li>
            Denominators are pairs (task, agent), not attempts: an agent that retries must not look
            better than one that does not.
          </li>
          <li>
            <code>null</code> means the denominator was zero: a zero denominator is not zero cost or
            zero acceptance. Acceptance counts decided pairs only; a pair no verdict exists for is
            unknown, not rejected. The verdict source is named in the table.
          </li>
          <li>
            A missing held-out result is a data defect, not a pass: those pairs leave the
            regression-free rate and are counted above.
          </li>
          <li>
            Judge tokens and cost are reported beside the agent's, never inside them.
          </li>
          <li>
            Costs computed under different <code>pricing_version</code> values are not comparable.
          </li>
        </ul>
      </div>
    </main>
  );
}
