# Job radar

Watches the careers pages of the companies you care about, three times a day, and
emails you when a job appears that matches what you're looking for.

No server, no subscription, nothing running on your laptop. It costs nothing and
runs on GitHub. **The product is an email — everything else is plumbing.**

---

## What an alert looks like

```
💼 2 new full-time roles

Monzo
  Senior Manager, Strategy & Operations  🔥
    London · Strategy & Operations · 2 days ago
    matched: strategy, operations

Airwallex
  Head of Partnerships, EMEA
    UK - London · Partnerships · 9 days ago
    matched: partnership
```

The 🔥 marks anything posted in the last three days. Applying in the first 48 hours
matters more than most people think.

**"matched:"** tells you which of your keywords let the job through. That's how you
tune the filters without reading any code: if three alerts in a row say
`matched: growth` and none of them interest you, you know exactly what to remove.

---

## Setting it up

You need a **GitHub account**. That's it — no credit card, no other tools.

### 1. Make your own copy

Click the green **Use this template** button at the top of this page, then
**Create a new repository**. Choose **Private** — a job search isn't something you
want indexed by Google.

### 2. Let it write to your repository

Go to **Settings → Actions → General**, scroll to **Workflow permissions**, pick
**Read and write permissions**, and save.

> This is the step everyone forgets, and the one that makes the first run fail for
> no obvious reason. The radar needs permission to keep notes and to open the issue
> that becomes your email.

### 3. Edit one file

Open `config.yaml`. It ships with two real companies and a working set of filters,
so you can run it before changing anything. Three things to set:

- **Your GitHub username** under `notify`
- **Your companies** (see below)
- **The roles you want** and the ones you don't

Nothing else in the repository ever needs to change.

### 4. Run it once

Go to the **Actions** tab, click **Job radar** in the left column, then
**Run workflow** on the right, and leave the mode on `normal`.

The first run is quiet on purpose. It writes down everything that's currently open
so it knows what "new" means from then on. Without that, your first email would
arrive with hundreds of jobs in it.

### 5. Check the email actually arrives

**Run workflow** again, this time with the mode set to `prueba`. It sends you a
sample alert built from real openings. If it lands in your inbox, everything works.
Close the issue and you're done.

From then on it runs on its own, weekday mornings, midday and evenings, and only
writes when there's something worth telling you.

---

## Adding your companies

Every company posts jobs through some hiring platform. There are about a dozen of
them, and the name a company uses on that platform is almost never just its name.
So don't guess — ask:

```
python3 radar.py --discover https://www.somecompany.com/careers
```

It answers with the exact lines to paste into `config.yaml`, then checks that the
connection works and shows you two real job titles:

```
── greenhouse ──
    platform: greenhouse
    board: monzo
    ✓ verified: 72 roles → Android Engineer · Backend Engineer III
      Read those titles. If they do not look like this company, the id is wrong.
```

**Read the two titles.** This is the one check worth doing every time. A wrong id
can land on a *different company's* job board — one that responds normally and looks
completely healthy. It happened here: a payments company called Primer turned out to
share a name with a US school network, and the radar spent a week reading job ads for
teachers in Florida.

Some companies can't be read at all — a few platforms simply don't publish their
listings in any usable form. The command tells you when that's the case instead of
letting you find out the hard way. For those, sign up for the job alert on the
company's own careers page.

**Start with three or four companies, not fifty.** See real jobs arrive on day one.
Adding more later takes a minute.

---

## Tuning what you get

Two lists in `config.yaml` do the work.

**`titles_include`** — words that should get a job through. Matched from the start of
a word, so `partnership` finds "Partnerships" and `ops` finds "Operations".

**`titles_exclude`** — words that keep a job out. These win over the list above.

Start narrow and widen. It's far easier to add a keyword than to dig out of a flood.

The exclude list is the one that grows. Every time something irrelevant shows up,
look at its `matched:` line, and add the guilty word. After a couple of weeks it
settles down and the email becomes genuinely worth opening.

There's one thing worth knowing about locations. Keywords there are matched as whole
words, not fragments — because as a fragment, "uk" also matches "Ukraine", and a job
in Kyiv would look British. Small detail, but it's the kind that quietly ruins a list.

### Checking your changes

```
python3 radar.py --test
```

Runs your filters against the example jobs listed under `tests:` in `config.yaml`.
Takes a second, never touches the internet. Add a case whenever you catch a mistake
so it can't come back.

```
python3 radar.py --check
```

Contacts every company, shows how many jobs each one has and how many match, and
**saves nothing**. This is what you run after editing the config, and every few weeks
to confirm nothing has quietly broken.

---

## Silence should never be ambiguous

This is the part that decides whether a tool like this is worth having.

The real risk isn't a crash — it's a company's careers page changing, the radar
quietly finding nothing there, and you reading the silence as "no new jobs" when it
actually means "broken". That's worse than having nothing, because it stops you
checking by hand.

So the radar refuses to be quietly wrong:

- If a source **fails**, **returns nothing**, or **suddenly returns far fewer jobs
  than usual**, the email says so instead of staying silent.
- If one company's site is down, the other fifty keep working. A single failure never
  takes down a run.
- Every email carries a table of every source and how many jobs it saw. One glance
  tells you everything is alive.
- **Every Monday morning** you get a digest of everything currently open that matches
  your filters, whether or not there's news. It arrives even on a quiet week — so a
  week with no email means something is wrong, not that nothing was posted.

By default you also get a short "nothing new" note each morning, for the same reason.
If that turns out to be too much mail, change `notify_when_nothing_new` to `never`.

---

## Running it on your own computer

Optional. The radar runs fine on GitHub without any of this, but if you want to test
filter changes quickly, you'll need [Python](https://www.python.org/downloads/) and:

```
pip install -r requirements.txt
```

Then any of `--test`, `--check`, `--discover`, `--weekly` or `--demo`.

`--reset` wipes the radar's memory and starts over. You'd only need it if something
got corrupted.

---

## The four files

| File | What it is | Do you touch it? |
|---|---|---|
| `config.yaml` | Your companies and filters | **Yes — this is the only one** |
| `radar.py` | Does the work | No |
| `state.json` | Which jobs it has already seen | Never, it manages itself |
| `.github/workflows/radar.yml` | The schedule | Only to change the times |

### Changing the schedule

Open `.github/workflows/radar.yml` and look for the lines starting with `cron`. The
times are in UTC, and the odd-looking `:55` is deliberate: GitHub queues these jobs,
and the top of the hour is the worst possible moment because that's when everyone
else has scheduled theirs. Running at :55 cuts the delay a lot.

Scheduled runs can still be a few minutes late. That's normal, not a fault.

---

## Supported platforms

You don't need to know any of this — `--discover` handles it. It's here for
reference.

| The Apply button points to… | `platform:` | What to put |
|---|---|---|
| `boards.greenhouse.io/acme` | `greenhouse` | `board: acme` |
| `jobs.ashbyhq.com/acme` | `ashby` | `board: acme` |
| `jobs.lever.co/acme` | `lever` | `board: acme` |
| `jobs.smartrecruiters.com/Acme` | `smartrecruiters` | `board: Acme` |
| `apply.workable.com/acme` | `workable` | `board: acme` |
| `acme.bamboohr.com/careers` | `bamboohr` | `board: acme` |
| `acme.pinpointhq.com/…` | `pinpoint` | `board: acme` |
| `careers.acme.com` (Teamtailor) | `teamtailor` | `board: careers.acme.com` |
| `candidate.hr-manager.net/…?customer=acme` | `hrmanager` | `board: acme` |
| `acme.wd5.myworkdayjobs.com/…/Acme_Careers` | `workday` | `tenant` + `pod` + `site` |
| `acme.fa.oraclecloud.com/…/sites/CX_1` | `oracle` | `host` + `site` |
| `acme.eightfold.ai/careers` | `eightfold` | `tenant` + `domain` |
| `amazon.jobs` | `amazon` | `country: GBR` |

Copy the id **exactly** as it appears. Some are capitalised, some contain dots or
hyphens that look like typos and aren't: `checkout.com`, `primer.io`, `iwoca.co.uk`,
`allica-bank`, `moneyboxapp`.

Two things worth knowing when a company seems to be missing:

**Check who bought them.** Worldpay had no careers site of its own because Global
Payments acquired them — and Global Payments' listings live under `tsys`, the name of
a company they merged with years earlier.

**Very large employers still work.** Some post thousands of jobs worldwide, which
would take forever to read. For the two biggest platforms the radar asks the employer
to send back only your region, which turns a seven-thousand-job catalogue into a few
hundred and a several-minute download into a few seconds. That happens automatically
when `locations_source` is set.

### Platforms that can't be read

A handful of hiring platforms don't publish their listings in any form a program can
read. `--discover` recognises them and says so: **Deel**, **JazzHR**, **join.com**,
**Cornerstone**, **HiBob** and **Factorial HR**. Some individual companies also block
automated requests outright.

There's no clever way around this, and trying is a trap: anything built on scraping a
web page breaks the day the page is redesigned, and breaks *silently*, which is the
one thing this project is built to avoid. Use the company's own job alert instead.

---

## What this deliberately isn't

No database — a single file is plenty. No dashboard — the product is an email. No
tracking of jobs that close, which sounds useful and mostly isn't. And no scraping,
for the reason above.

Keeping it this small is why it still works.
