# Model Comparison Report

**Generated:** May 07, 2026
**Runs compared:** Sonnet, Opus, Nova Pro, Textract
**Files processed:** 12 restaurant menus

---

## Overall Performance

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Success Rate | 12/12 | 12/12 | 12/12 | 12/12 |
| Duration | 82.4s | 84.1s | 69.2s | 87.7s |
| Total Tokens | 133,736 | 162,954 | 135,044 | 65,656 |
| **Total Cost** | **$0.9038** | **$3.6159** | **$0.3151** | **$0.6281** |

---

## Aggregate Quality Metrics

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| **Total Items Extracted** | **599** | **634** | **596** | **610** |
| Items with Price | 559 (93%) | 605 (95%) | 551 (92%) | 561 (91%) |
| Items with Description | 394 (65%) | 446 (70%) | 390 (65%) | 351 (57%) |
| Items with Dietary Info | 354 (59%) | 347 (54%) | 173 (29%) | 407 (66%) |
| **Cost per Item** | $0.0015 | $0.0057 | $0.0005 | $0.0010 |

---

## Per-File Breakdown

### El Foratsero-Presentation

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | EL FORASTERO MEXICAN | EL FORASTERO MEXICAN | EL FORASTERO MEXICAN | EL FORASTERO MEXICAN FOOD |
| Categories | 21 | 22 | 21 | 24 |
| Total Items | 105 | 118 | 106 | 106 |
| Items w/ Description | 105 | 118 | 106 | 68 |
| Items w/ Dietary | 44 | 46 | 44 | 42 |
| Price Range | $1.00 – $18.99 | $0.50 – $18.99 | $0.99 – $18.99 | $0.75 – $18.99 |
| Processing Time | 38.6s | 40.7s | 39.0s | 52.5s |

### IMG_4475

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | India Garden | India Garden | India Garden | Odia Garden |
| Categories | 4 | 4 | 4 | 4 |
| Total Items | 54 | 54 | 56 | 54 |
| Items w/ Description | 4 | 4 | 1 | 5 |
| Items w/ Dietary | 41 | 42 | 0 | 41 |
| Price Range | $1.99 – $40.99 | $1.99 – $40.99 | $1.99 – $40.99 | $1.99 – $40.99 |
| Processing Time | 26.9s | 26.6s | 21.1s | 20.9s |

### IMG_4477

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Taj Mahal Livermore | Taj Mahal Livermore | — | Taj Mahal |
| Categories | 3 | 3 | 3 | 3 |
| Total Items | 10 | 10 | 10 | 10 |
| Items w/ Description | 10 | 10 | 10 | 10 |
| Items w/ Dietary | 10 | 10 | 0 | 10 |
| Price Range | $9.99 – $14.99 | $9.99 – $14.99 | $9.99 – $14.99 | $9.99 – $14.99 |
| Processing Time | 18.7s | 21.5s | 7.9s | 13.2s |

### IMG_4478

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Taj Mahal Livermore | Taj Mahal Livermore | Indo Chinese | Taj Mahal |
| Categories | 2 | 2 | 2 | 2 |
| Total Items | 14 | 14 | 14 | 14 |
| Items w/ Description | 14 | 14 | 14 | 14 |
| Items w/ Dietary | 14 | 13 | 0 | 12 |
| Price Range | $13.99 – $21.99 | $13.99 – $21.99 | $15.99 – $21.99 | $13.99 – $21.99 |
| Processing Time | 18.8s | 21.4s | 9.2s | 14.1s |

### IMG_4479

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Taj Mahal Livermore | Taj Mahal Livermore | Veg Main Course | Taj Mahal |
| Categories | 2 | 2 | 2 | 2 |
| Total Items | 12 | 12 | 12 | 12 |
| Items w/ Description | 12 | 12 | 12 | 12 |
| Items w/ Dietary | 12 | 8 | 0 | 8 |
| Price Range | $16.99 – $34.99 | $16.99 – $34.99 | $16.99 – $27.99 | $16.99 – $34.99 |
| Processing Time | 19.5s | 21.4s | 9.4s | 12.6s |

### IMG_4480

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Unknown (not visible on m | Mexican Restaurant | Al Pastor Restaurant | CHOICE OF MEAT |
| Categories | 6 | 6 | 6 | 9 |
| Total Items | 34 | 34 | 34 | 55 |
| Items w/ Description | 34 | 34 | 34 | 35 |
| Items w/ Dietary | 6 | 7 | 0 | 5 |
| Price Range | $3.00 – $20.00 | $3.00 – $20.00 | $3.00 – $20.00 | $3.00 – $20.00 |
| Processing Time | 25.1s | 25.3s | 20.8s | 19.4s |

### IMG_9591 2

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Unknown | Unknown | — | — |
| Categories | 1 | 1 | 1 | 1 |
| Total Items | 7 | 7 | 7 | 7 |
| Items w/ Description | 7 | 7 | 7 | 7 |
| Items w/ Dietary | 7 | 7 | 0 | 7 |
| Price Range | $8.99 – $10.99 | $8.99 – $10.99 | $8.99 – $10.99 | $8.99 – $10.99 |
| Processing Time | 9.3s | 15.2s | 5.9s | 5.8s |

### Kabila Restaurant

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Deliciious Traditional Fo | Delicious Traditional Foo | DELICIIOUS TRADITIONAL FO | Kabila Traditional Restau |
| Categories | 16 | 16 | 15 | 16 |
| Total Items | 172 | 197 | 174 | 163 |
| Items w/ Description | 160 | 197 | 161 | 150 |
| Items w/ Dietary | 100 | 95 | 98 | 162 |
| Price Range | $3.00 – $25.00 | $3.00 – $25.00 | $3.00 – $25.00 | $3.00 – $25.00 |
| Processing Time | 58.7s | 62.0s | 59.3s | 73.6s |

### MASTER G menu

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | MASTER G | MASTER G | MASTER G | MASTER G |
| Categories | 7 | 7 | 7 | 7 |
| Total Items | 40 | 40 | 40 | 40 |
| Items w/ Description | 31 | 32 | 31 | 32 |
| Items w/ Dietary | 31 | 31 | 31 | 32 |
| Price Range | $1.99 – $14.99 | $1.99 – $14.99 | $1.99 – $14.99 | $1.99 – $14.99 |
| Processing Time | 18.8s | 18.1s | 18.1s | 23.6s |

### RAJASTHANI PAGE AI File

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Garam Mirchi | Garam Mirchi | Garam Mirchi | GARAM MIRCHI |
| Categories | 4 | 3 | 3 | 5 |
| Total Items | 41 | 41 | 35 | 41 |
| Items w/ Description | 3 | 4 | 2 | 4 |
| Items w/ Dietary | 41 | 41 | 0 | 40 |
| Price Range | $2.99 – $21.99 | $2.99 – $21.99 | $2.99 – $19.99 | $2.99 – $21.99 |
| Processing Time | 27.6s | 27.0s | 18.1s | 17.9s |

### biriyani_junction_2

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | Unknown Indian Restaurant | Indian Restaurant | Rajas | Vijayawada Special |
| Categories | 8 | 8 | 5 | 8 |
| Total Items | 98 | 95 | 96 | 96 |
| Items w/ Description | 2 | 2 | 0 | 2 |
| Items w/ Dietary | 37 | 36 | 0 | 37 |
| Price Range | $3.99 – $14.99 | $3.99 – $14.99 | $3.99 – $14.99 | $3.99 – $14.99 |
| Processing Time | 43.7s | 35.7s | 31.8s | 27.7s |

### menu1

| Metric | Sonnet | Opus | Nova Pro | Textract |
|--------|-------|-------|-------|-------|
| Restaurant | MASTER G | Master G | MASTER G menu | MASTER G |
| Categories | 1 | 1 | 1 | 1 |
| Total Items | 12 | 12 | 12 | 12 |
| Items w/ Description | 12 | 12 | 12 | 12 |
| Items w/ Dietary | 11 | 11 | 0 | 11 |
| Price Range | $4.99 – $12.99 | $4.99 – $12.99 | $4.99 – $12.99 | $4.99 – $12.99 |
| Processing Time | 15.9s | 15.6s | 6.9s | 5.6s |

---

## Recommendation

**Based on this run's results:**

| Metric | Winner | Value |
|--------|--------|-------|
| Most items extracted | Opus | 634 items |
| Best accuracy (price + dietary) | Textract | 561 priced, 407 dietary |
| Lowest total cost | Nova Pro | $0.3151 |
| Best cost per item | Nova Pro | $0.00053/item |
| Fastest | Nova Pro | 69.2s |

**Use case guidance:**

| Use Case | Recommended | Rationale |
|----------|-------------|-----------|
| Production (accuracy + cost) | Nova Pro | Best cost-per-item with strong quality |
| Maximum coverage | Opus | Extracts the most items from menus |
| Budget-constrained bulk | Nova Pro | Lowest total cost across all files |
| Speed-critical | Nova Pro | Fastest total processing time |
