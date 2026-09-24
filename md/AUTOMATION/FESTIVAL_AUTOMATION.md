# Festival automation

Related: [Festival MCP](../MCP/FESTIVAL_MCP.md), [Scheduler](SCHEDULER.md), [Database](../DATABASE/DATABASE.md).

## Source of dates

The scheduler catalog is the `FESTIVALS` list in `festivals/india_festivals.py`. Lunar and movable dates are explicit civil dates. They are not computed from an astronomical library and DeepSeek is not asked for them. Festival copy and creative direction use the DeepSeek content plan (**IMPLEMENTED — NOT CONFIGURED** while `DEEPSEEK_API_KEY` is empty). Publishing, when approved, is the Instagram Agent only.

`festivals/data/lunar_dates.json` overrides a year when `LUNAR_KEYS` maps the festival name and that year exists in the JSON. If a lunar festival has no date for the requested year, `occurrence_for_year` returns nothing and that festival is skipped.

Timezone for campaign math: `Asia/Kolkata`.

`festivals/data/coverage.json` is read by `festivals/intelligence.py` for region and retail/F&B labels. It does not add rows to `FESTIVALS`. Names that exist only in coverage are not scheduled.

## Catalog in `FESTIVALS` (47)

Fixed month/day (`lunar: false` and no year table), unless noted:

| Name | Region | Date rule |
| --- | --- | --- |
| New Year | National | 1 January |
| Makar Sankranti / Pongal | National | 14 January |
| Republic Day | National | 26 January |
| Independence Day | National | 15 August |
| Gandhi Jayanti | National | 2 October |
| Baisakhi | Punjab | 13 April |
| Vishu | Kerala | 14 April |
| Bohag Bihu | Assam | 14 April |
| Labour Day | National | 1 May |
| Teachers' Day | National | 5 September |
| Constitution Day | National | 26 November |
| Children's Day | National | 14 November |
| Christmas | National | 25 December |

Year-keyed dates in the module (2025–2027, some through 2028). `lunar: true` except Lohri and Pongal, which are tagged `lunar: false` but still use a `dates` map:

Maha Shivaratri, Holi, Ugadi / Gudi Padwa, Ram Navami, Mahavir Jayanti, Good Friday, Eid ul-Fitr, Akshaya Tritiya, Buddha Purnima, Eid ul-Adha, Rath Yatra (East), Raksha Bandhan, Janmashtami, Ganesh Chaturthi (West), Onam (Kerala), Navratri / Durga Puja start, Dussehra / Vijayadashami, Karva Chauth (North), Diwali, Govardhan Puja / Annakut, Bhai Dooj, Chhath Puja (East), Guru Nanak Jayanti, Lohri (Punjab), Pongal (Tamil Nadu), Vasant Panchami (North), Holika Dahan, Easter, Muharram, Guru Purnima, Mahalaya (West Bengal), Durga Puja (West Bengal), Kali Puja (West Bengal), Karthigai Deepam (Tamil Nadu).

Eid and Muharram descriptions say to confirm locally. The code still uses the stored civil date. It does not read a moon-sighting feed.

Regions stored on rows: National, South / West, East, West, Kerala, North, Punjab, Tamil Nadu, Assam, West Bengal.

## Campaign rules (`festivals/campaign_rules.py`)

| Constant | Value |
| --- | --- |
| `DEFAULT_REQUIRED_POSTS` | 2 |
| `DEFAULT_PRE_FESTIVAL_DAYS` | 1 |
| `CATCH_UP_DAYS` | 7 |
| Counted status | `PUBLISHED` only |

Ignored for the count: `FAILED`, `AMBIGUOUS_PUBLICATION`, `PUBLISHING`, `PENDING`, `GENERATED`.

Phases: pre-festival (local day minus `pre_festival_days`), festival day, then catch-up on the following days through day +7.

Uniqueness: one campaign per user, case-folded festival name, and year. One slot per local calendar day unless `allow_same_day_festival_posts` is set. Same-day collapse also skips when a post is `PUBLISHED`, `AMBIGUOUS_PUBLICATION`, or `PUBLISHING`.

`festival_posts_per_festival` on `automation_settings` overrides the default required count when the campaign is created.

## Scheduler (`scheduler/festival_scheduler.py`)

Skip when festival automation is disabled. `ensure_campaigns` for the clock year. `due_campaigns` selects pre, day, and catch-up windows. Remaining posts `max(0, required - published)`. Scheduled local time is 10:00. The pipeline runs once per due slot. Only `PUBLISHED` increments `published_posts`.

Failure leaves the festival post in a non-published status so the next tick can retry until the catch-up window ends. An ambiguous publish is not counted and is not immediately republished.

Auto-publish requires `auto_festival_publish` plus the shared approval gates. Otherwise the image waits for human approval.

## API

| Method | Path |
| --- | --- |
| GET | `/api/v1/festivals` — catalog for the clock year in Asia/Kolkata |
| GET | `/api/v1/festivals/campaigns` — this user's campaigns; may call `ensure_campaigns` when festival automation is enabled |
| PUT | `/api/v1/festivals/settings` — festival keys on `automation_settings` |

## Tests

`tests/test_festival_intelligence.py`, `tests/test_scheduler.py`.
