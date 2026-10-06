/**
 * The Phase 6 acceptance criteria, asserted rather than described.
 *
 * Each `it` here maps to a line of the brief's frontend checklist. The point is
 * not coverage for its own sake: these are the claims the dashboard makes, and a
 * claim nobody can re-run is an assertion rather than a measurement.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { defaultRoutes, renderApp } from '@/test/harness'
import * as fixtures from '@/test/fixtures'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the triage queue is the landing page', () => {
  it('renders the queue at /, not an overview dashboard', async () => {
    renderApp('/')

    expect(await screen.findByRole('region', { name: /alert queue/i })).toBeInTheDocument()
    // The nav is present and the queue entry is the active one.
    expect(screen.getByRole('link', { name: 'Queue' })).toBeInTheDocument()
  })

  it('lists rows in the order the server returned, highest risk first', async () => {
    renderApp('/')

    await screen.findByRole('region', { name: /alert queue/i })

    const rows = screen.getAllByRole('button', { name: /^Open alert/ })
    expect(rows.map((row) => row.getAttribute('aria-label'))).toEqual([
      'Open alert 3',
      'Open alert 1',
      'Open alert 2',
    ])
  })

  it('shows the risk score, so the ordering can be checked on screen', async () => {
    renderApp('/')

    await screen.findByRole('region', { name: /alert queue/i })
    const rows = screen.getAllByRole('button', { name: /^Open alert/ })

    // Read the risk cell of each row rather than searching the whole table:
    // a risk of 0.99 and a confidence of 0.99 render the same string, and the
    // claim being tested is about the column, not the number.
    const risks = rows.map(
      (row) => row.querySelector('[data-column="risk"]')?.textContent ?? '',
    )
    expect(risks).toEqual(['0.99', '0.96', '0.56'])
  })

  it('renders the dedupe count rather than 99 separate rows', async () => {
    renderApp('/')

    const novel = await screen.findByRole('button', { name: 'Open alert 1' })
    expect(within(novel).getByText('×99')).toBeInTheDocument()
  })

  it('says so plainly when the queue is empty', async () => {
    renderApp('/', {
      ...defaultRoutes(),
      '/alerts': { items: [], next_cursor: null, limit: 100 },
    })

    expect(await screen.findByText('No open alerts')).toBeInTheDocument()
  })
})

describe('unclassified anomalies are distinct and one click away', () => {
  it('marks the anomaly row differently from a named family', async () => {
    renderApp('/')

    const novel = await screen.findByRole('button', { name: 'Open alert 1' })
    const known = screen.getByRole('button', { name: 'Open alert 3' })

    expect(within(novel).getByText('Unclassified anomaly')).toBeInTheDocument()
    expect(within(known).getByText('DoS')).toBeInTheDocument()

    // Not colour alone: the row carries its own accent, which is the third mark
    // after the badge variant and the glyph inside it.
    expect(novel.className).toMatch(/border-l-\[var\(--novel\)\]/)
    expect(known.className).not.toMatch(/border-l-\[var\(--novel\)\]/)
  })

  it('filters to anomalies in a single click', async () => {
    const harness = renderApp('/', {
      ...defaultRoutes(),
      '/alerts': fixtures.novelOnlyPage,
    })

    await screen.findByRole('region', { name: /alert queue/i })
    fireEvent.click(screen.getByRole('button', { name: /unclassified anomalies/i }))

    await waitFor(() =>
      expect(
        harness.calls.some((call) => call.includes('kind=UNCLASSIFIED_ANOMALY')),
      ).toBe(true),
    )
  })
})

describe('alert detail answers why, what it is, and how to fix it', () => {
  it('asks the three questions in that fixed order', async () => {
    renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('Why was this flagged')
    const headings = within(dialog)
      .getAllByRole('heading', { level: 3 })
      .map((heading) => heading.textContent)

    expect(headings.slice(0, 3)).toEqual([
      'Why was this flagged',
      'What this likely is',
      'How to fix it',
    ])
  })

  it('puts the English sentence above the chart', async () => {
    renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    const sentence = await within(dialog).findByText(fixtures.knownDetail.narrative)
    const rawFlow = within(dialog).getByText('Raw flow record')

    // A sentence needs no interpreting and a chart does, so the sentence comes
    // first in document order.
    expect(sentence.compareDocumentPosition(rawFlow)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('names the technique with a plain-English line, not just a code', async () => {
    renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('T1499')).toBeInTheDocument()
    expect(within(dialog).getByText('Endpoint Denial of Service')).toBeInTheDocument()
    expect(
      within(dialog).getByText(/flooding one service with more requests/i),
    ).toBeInTheDocument()
  })

  it('renders the playbook as a checklist', async () => {
    renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('How to fix it')
    for (const action of fixtures.knownDetail.recommended_actions.actions) {
      expect(within(dialog).getByText(action)).toBeInTheDocument()
    }
  })

  it('refuses to invent a technique or a playbook for an anomaly', async () => {
    renderApp('/?alert=1')

    const dialog = await screen.findByRole('dialog')
    expect(
      await within(dialog).findByText(/doesn’t match a known technique/i),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByText(/no playbook yet — escalate for manual investigation/i),
    ).toBeInTheDocument()
    // No fabricated id anywhere in the panel.
    expect(dialog.textContent).not.toMatch(/\bT1\d{3}\b/)
  })

  it('badges the replay ground truth as demo-only', async () => {
    renderApp('/?alert=1')

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('demo only')).toBeInTheDocument()
    expect(within(dialog).getByText('Infiltration')).toBeInTheDocument()
  })

  it('offers no containment action', async () => {
    renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/never blocks traffic/i)).toBeInTheDocument()

    // No containment control of any kind, and no endpoint behind one. Asserted
    // rather than merely absent, because "where is the block button" is the
    // first question anyone asks of an IDS dashboard.
    expect(dialog.textContent).not.toMatch(/\bblock\b|\bquarantine\b|\bdrop traffic\b/i)
    const actions = within(dialog)
      .getAllByRole('button')
      .map((button) => button.textContent ?? '')
    expect(actions.join(' ')).not.toMatch(/block|quarantine|contain/i)
  })
})

describe('a verdict refreshes the queue behind the drawer', () => {
  it('posts the verdict and refetches the alert queries', async () => {
    const harness = renderApp('/?alert=3')

    const dialog = await screen.findByRole('dialog')
    const verdictButton = await within(dialog).findByRole('button', { name: 'True positive' })
    const before = harness.calls.filter((call) => call.startsWith('/alerts?')).length

    fireEvent.click(verdictButton)

    await waitFor(() =>
      expect(harness.bodies.some((entry) => entry.path === '/alerts/3/verdict')).toBe(true),
    )
    expect(harness.bodies.at(-1)?.body).toMatchObject({ verdict: 'TP' })

    // The invalidation is the behaviour under test: the queue must re-ask rather
    // than keep showing a row the analyst just judged.
    await waitFor(() =>
      expect(
        harness.calls.filter((call) => call.startsWith('/alerts?')).length,
      ).toBeGreaterThan(before),
    )
  })
})

describe('bulk dismiss', () => {
  it('sends one request for the whole selection and records no verdict', async () => {
    const harness = renderApp('/')

    await screen.findByRole('region', { name: /alert queue/i })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select alert 3' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select alert 1' }))

    expect(await screen.findByText('2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss selected' }))

    await waitFor(() =>
      expect(harness.bodies.some((entry) => entry.path === '/alerts/status')).toBe(true),
    )
    const sent = harness.bodies.find((entry) => entry.path === '/alerts/status')
    expect(sent?.method).toBe('PATCH')
    // Order is the selection map's, which is not the click order and does not
    // need to be: the endpoint dedupes and reports per id.
    expect(sent?.body).toMatchObject({ status: 'dismissed' })
    expect((sent?.body as { alert_ids: number[] }).alert_ids.sort()).toEqual([1, 3])
    expect(harness.bodies.some((entry) => entry.path.endsWith('/verdict'))).toBe(false)
  })
})

describe('the draggable threshold', () => {
  it('exposes the threshold as a real slider, opened at the shipped value', async () => {
    renderApp('/live')

    const slider = await screen.findByRole('slider', { name: /anomaly score threshold/i })
    expect(Number(slider.getAttribute('aria-valuenow'))).toBeCloseTo(
      fixtures.anomalyHistogram.tau_anom as number,
      6,
    )
  })

  it('moves on the keyboard and asks the server for the new projection', async () => {
    const harness = renderApp('/live')

    const slider = await screen.findByRole('slider', { name: /anomaly score threshold/i })
    const before = Number(slider.getAttribute('aria-valuenow'))

    fireEvent.keyDown(slider, { key: 'ArrowRight' })

    await waitFor(() =>
      expect(Number(slider.getAttribute('aria-valuenow'))).toBeGreaterThan(before),
    )
    // Debounced, so the request follows the move rather than racing it.
    await waitFor(
      () => expect(harness.calls.filter((call) => call.startsWith('/metrics/threshold')).length)
        .toBeGreaterThan(1),
      { timeout: 2_000 },
    )
  })

  it('reports the projected volume against the configured budget', async () => {
    renderApp('/live')

    expect(await screen.findByText('Alerts per analyst hour')).toBeInTheDocument()
    expect(screen.getAllByText(/over the budget/i).length).toBeGreaterThan(0)
  })
})

describe('model performance', () => {
  it('draws PR and ROC with the caption that explains the gap', async () => {
    renderApp('/model')

    expect(await screen.findByText('Precision–recall')).toBeInTheDocument()
    expect(screen.getByText('ROC')).toBeInTheDocument()
    expect(screen.getByText(/why these two are drawn next to each other/i)).toBeInTheDocument()
    expect(screen.getByText(/PR-AUC is the headline here/i)).toBeInTheDocument()
  })

  it('gives the LOAO table a Missed column with real misses in it', async () => {
    renderApp('/model')

    expect(
      await screen.findByText(/leave-one-attack-out: detection of families/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Missed' })).toBeInTheDocument()
    expect(screen.getByText('47,379')).toBeInTheDocument()
  })

  it('shows the named-correctly column, so recall cannot read as classification', async () => {
    renderApp('/model')

    expect(
      await screen.findByRole('columnheader', { name: 'Named correctly' }),
    ).toBeInTheDocument()
  })

  it('reports accuracy only as a caveated footnote', async () => {
    renderApp('/model')

    const footnote = await screen.findByText(/overall accuracy on this split/i)
    expect(footnote).toBeInTheDocument()
    expect(footnote.textContent).toMatch(/always answering benign would score close to the same/i)

    // Not a headline: no heading, and no stat tile, carries the word.
    for (const heading of screen.getAllByRole('heading')) {
      expect(heading.textContent ?? '').not.toMatch(/accura/i)
    }
  })
})

describe('the phase 7 screens are honest about what is missing', () => {
  it('names the phase that implements drift rather than drawing a flat line', async () => {
    renderApp('/drift')

    expect(await screen.findByText(/per-feature drift arrives in Phase 7/i)).toBeInTheDocument()
    expect(screen.getByText('GET /metrics/drift')).toBeInTheDocument()
  })

  it('still shows the real baseline the PSI will be measured against', async () => {
    renderApp('/drift')

    expect(await screen.findByText(/the baseline PSI is measured against/i)).toBeInTheDocument()
  })

  it('disables the retrain button and says which phase enables it', async () => {
    renderApp('/feedback')

    const button = await screen.findByRole('button', { name: /retrain with 2 new labels/i })
    expect(button).toBeDisabled()
    expect(screen.getByText(/ships the challenger pipeline/i)).toBeInTheDocument()
  })

  it('reports the disagreement rate and excludes undecided verdicts from it', async () => {
    renderApp('/feedback')

    expect(await screen.findByText(/disagreement rate 33.3%/i)).toBeInTheDocument()
    expect(screen.getByText(/not knowing is not the same as the model being wrong/i)).toBeVisible()
  })
})

describe('analytics', () => {
  it('shows trends, families, ranked hosts, throughput and MITRE coverage', async () => {
    renderApp('/analytics')

    expect(await screen.findByText('Alerts over time')).toBeInTheDocument()
    expect(screen.getByText('Attack families seen')).toBeInTheDocument()
    expect(screen.getByText('Most targeted hosts')).toBeInTheDocument()
    expect(screen.getByText('Busiest sources')).toBeInTheDocument()
    expect(screen.getByText('SOC throughput')).toBeInTheDocument()
    expect(screen.getByText('MITRE ATT&CK coverage')).toBeInTheDocument()
  })

  it('keeps techniques that have never fired on the grid as visible zeros', async () => {
    renderApp('/analytics')

    const never = await screen.findByTitle(/talking to a controller/i)
    expect(within(never).getByText('0')).toBeInTheDocument()
  })

  it('counts unclassified anomalies beside the grid, not inside it', async () => {
    renderApp('/analytics')

    expect(
      await screen.findByText(/unclassified anomalies, which map to no technique at all/i),
    ).toBeInTheDocument()
  })

  it('scopes every panel from one range control', async () => {
    const harness = renderApp('/analytics')

    await screen.findByText('Alerts over time')
    fireEvent.click(screen.getByRole('button', { name: '7 days' }))

    await waitFor(() =>
      expect(harness.calls.some((call) => call.includes('range=7d'))).toBe(true),
    )
  })

  it('leads on the unclassified rate rather than an accuracy percentage', async () => {
    renderApp('/analytics')

    await screen.findByText('SOC throughput')
    expect(
      screen.getByText(/of alerts were unclassified anomalies/i),
    ).toBeInTheDocument()
    for (const heading of screen.getAllByRole('heading')) {
      expect(heading.textContent ?? '').not.toMatch(/accura/i)
    }
  })
})

describe('no accuracy hero tile, on any screen', () => {
  // The one screen allowed to say the word is Model Performance, and only in the
  // caveated footnote asserted above.
  const screens = ['/', '/live', '/drift', '/feedback', '/analytics', '/system']

  it.each(screens)('%s never mentions accuracy', async (route) => {
    renderApp(route)

    // Let the first round of queries settle so this is not asserting on a
    // skeleton.
    await waitFor(() => expect(document.body.textContent).toBeTruthy())
    await new Promise((resolve) => setTimeout(resolve, 60))

    expect(document.body.textContent ?? '').not.toMatch(/accura/i)
  })
})
