#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define LIVE_CARD_COUNT 40
#define PAIR_COUNT 780
#define FLOP_COUNT 9880
#define ORDERED_TURN_RIVER_COUNT 1332
#define TOTAL_BOARD_COUNT 658008
#define DEALER_COMBOS_PER_BOARD 595
#define BOARD_CACHE_SIZE (1u << 21)
#define FLOP_CACHE_SIZE (1u << 15)

enum {
    CAT_HIGH_CARD = 0,
    CAT_ONE_PAIR = 1,
    CAT_TWO_PAIR = 2,
    CAT_TRIPS = 3,
    CAT_STRAIGHT = 4,
    CAT_FLUSH = 5,
    CAT_FULL_HOUSE = 6,
    CAT_QUADS = 7,
    CAT_STRAIGHT_FLUSH = 8,
};

typedef struct {
    uint8_t a;
    uint8_t b;
    uint64_t mask;
} PairIdx;

typedef struct {
    uint8_t a;
    uint8_t b;
    uint8_t c;
    uint64_t mask;
} TripleIdx;

typedef struct {
    uint8_t rank_counts[13];
    uint8_t suit_counts[4];
    uint16_t suit_rank_masks[4];
    uint16_t rank_mask;
} BoardState;

typedef struct {
    uint32_t score;
    uint8_t category;
    uint8_t primary;
} EvalResult;

typedef struct {
    uint64_t key;
    double ev1;
    double ev2;
    double ev4;
} BoardCacheEntry;

typedef struct {
    uint64_t key;
    double ev4;
    double ev2;
    double ev_check;
    double ev;
} FlopCacheEntry;

static const char *DEFAULT_HERO_STRS[2] = {"2d", "2s"};
static const char *DEFAULT_EXPOSED_STRS[10] = {
    "8c", "Ac", "4h", "9d", "Qh",
    "7c", "Td", "Jc", "3h", "8s"
};

/*
 * Full-game EV convention:
 * - Ante and blind are both live forced bets.
 * - A river fold forfeits both, so folding is -2.0.
 * This matches standard UTH accounting and the target 4x EV.
 */
static const double FOLD_EV = -2.0;

static uint8_t card_rank[52];
static uint8_t card_suit[52];
static uint64_t card_bit[52];

static uint8_t hero_cards[2];
static uint8_t exposed_cards[10];
static uint64_t dead_mask = 0;
static uint8_t live_cards[LIVE_CARD_COUNT];
static PairIdx pair_indices[PAIR_COUNT];
static TripleIdx flop_indices[FLOP_COUNT];

static BoardCacheEntry *board_cache = NULL;
static FlopCacheEntry *flop_cache = NULL;

static char hero_text[2][3];
static char exposed_text[10][3];

static int audit_enabled = 0;
static uint64_t audit_board_cache_misses = 0;
static uint64_t audit_board_cache_hits = 0;
static uint64_t audit_board_call_count = 0;
static uint64_t audit_flop_count = 0;
static uint64_t audit_ordered_turn_river_count = 0;
static uint64_t audit_total_dealer_worlds = 0;

static inline double monotonic_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static inline uint64_t mix_u64(uint64_t x) {
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33;
    x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}

static inline int straight_high(uint16_t mask) {
    if ((mask & ((1u << 12) | 0x000fu)) == ((1u << 12) | 0x000fu)) {
        return 3;
    }
    for (int hi = 12; hi >= 4; --hi) {
        const uint16_t run = (uint16_t)(0x001fu << (hi - 4));
        if ((mask & run) == run) {
            return hi;
        }
    }
    return -1;
}

static inline uint32_t make_score(
    int category,
    int r1,
    int r2,
    int r3,
    int r4,
    int r5
) {
    return ((uint32_t)category << 24) |
           ((uint32_t)r1 << 20) |
           ((uint32_t)r2 << 16) |
           ((uint32_t)r3 << 12) |
           ((uint32_t)r4 << 8) |
           ((uint32_t)r5 << 4);
}

static inline int rank_at_least_one(
    const BoardState *board,
    int rank_a,
    int rank_b,
    int excluded
) {
    for (int r = 12; r >= 0; --r) {
        if (r == excluded) {
            continue;
        }
        const int cnt = board->rank_counts[r] + (rank_a == r) + (rank_b == r);
        if (cnt > 0) {
            return r;
        }
    }
    return 0;
}

static inline int rank_at_least_one_excluding_two(
    const BoardState *board,
    int rank_a,
    int rank_b,
    int excluded_a,
    int excluded_b
) {
    for (int r = 12; r >= 0; --r) {
        if (r == excluded_a || r == excluded_b) {
            continue;
        }
        const int cnt = board->rank_counts[r] + (rank_a == r) + (rank_b == r);
        if (cnt > 0) {
            return r;
        }
    }
    return 0;
}

static inline void top_flush_ranks(uint16_t mask, int out[5]) {
    int filled = 0;
    for (int r = 12; r >= 0 && filled < 5; --r) {
        if (mask & (1u << r)) {
            out[filled++] = r;
        }
    }
    while (filled < 5) {
        out[filled++] = 0;
    }
}

static inline EvalResult eval_with_two(
    const BoardState *board,
    uint8_t card_a,
    uint8_t card_b
) {
    const int rank_a = card_rank[card_a];
    const int suit_a = card_suit[card_a];
    const int rank_b = card_rank[card_b];
    const int suit_b = card_suit[card_b];

    uint16_t suit_masks[4] = {
        board->suit_rank_masks[0],
        board->suit_rank_masks[1],
        board->suit_rank_masks[2],
        board->suit_rank_masks[3],
    };
    int suit_counts[4] = {
        board->suit_counts[0],
        board->suit_counts[1],
        board->suit_counts[2],
        board->suit_counts[3],
    };

    suit_masks[suit_a] |= (uint16_t)(1u << rank_a);
    suit_masks[suit_b] |= (uint16_t)(1u << rank_b);
    ++suit_counts[suit_a];
    ++suit_counts[suit_b];

    const uint16_t rank_mask =
        board->rank_mask | (uint16_t)(1u << rank_a) | (uint16_t)(1u << rank_b);

    int flush_suit = -1;
    if (suit_counts[0] >= 5) {
        flush_suit = 0;
    } else if (suit_counts[1] >= 5) {
        flush_suit = 1;
    } else if (suit_counts[2] >= 5) {
        flush_suit = 2;
    } else if (suit_counts[3] >= 5) {
        flush_suit = 3;
    }

    if (flush_suit >= 0) {
        const int sf_high = straight_high(suit_masks[flush_suit]);
        if (sf_high >= 0) {
            EvalResult result = {
                .score = make_score(CAT_STRAIGHT_FLUSH, sf_high, 0, 0, 0, 0),
                .category = CAT_STRAIGHT_FLUSH,
                .primary = (uint8_t)sf_high,
            };
            return result;
        }
    }

    int quad = -1;
    int trips[2] = {-1, -1};
    int pair_count = 0;
    int pairs[3] = {-1, -1, -1};
    int singles[5] = {0, 0, 0, 0, 0};
    int single_count = 0;

    for (int r = 12; r >= 0; --r) {
        const int cnt = board->rank_counts[r] + (rank_a == r) + (rank_b == r);
        if (cnt == 4) {
            quad = r;
        } else if (cnt == 3) {
            if (trips[0] < 0) {
                trips[0] = r;
            } else if (trips[1] < 0) {
                trips[1] = r;
            }
        } else if (cnt == 2) {
            if (pair_count < 3) {
                pairs[pair_count] = r;
            }
            ++pair_count;
        } else if (cnt == 1) {
            if (single_count < 5) {
                singles[single_count] = r;
            }
            ++single_count;
        }
    }

    if (quad >= 0) {
        const int kicker = rank_at_least_one(board, rank_a, rank_b, quad);
        EvalResult result = {
            .score = make_score(CAT_QUADS, quad, kicker, 0, 0, 0),
            .category = CAT_QUADS,
            .primary = (uint8_t)quad,
        };
        return result;
    }

    if (trips[0] >= 0 && (trips[1] >= 0 || pair_count > 0)) {
        const int pair_rank = (trips[1] > pairs[0]) ? trips[1] : pairs[0];
        EvalResult result = {
            .score = make_score(CAT_FULL_HOUSE, trips[0], pair_rank, 0, 0, 0),
            .category = CAT_FULL_HOUSE,
            .primary = (uint8_t)trips[0],
        };
        return result;
    }

    if (flush_suit >= 0) {
        int ranks[5];
        top_flush_ranks(suit_masks[flush_suit], ranks);
        EvalResult result = {
            .score = make_score(CAT_FLUSH, ranks[0], ranks[1], ranks[2], ranks[3], ranks[4]),
            .category = CAT_FLUSH,
            .primary = (uint8_t)ranks[0],
        };
        return result;
    }

    const int straight = straight_high(rank_mask);
    if (straight >= 0) {
        EvalResult result = {
            .score = make_score(CAT_STRAIGHT, straight, 0, 0, 0, 0),
            .category = CAT_STRAIGHT,
            .primary = (uint8_t)straight,
        };
        return result;
    }

    if (trips[0] >= 0) {
        EvalResult result = {
            .score = make_score(CAT_TRIPS, trips[0], singles[0], singles[1], 0, 0),
            .category = CAT_TRIPS,
            .primary = (uint8_t)trips[0],
        };
        return result;
    }

    if (pair_count >= 2) {
        const int kicker = rank_at_least_one_excluding_two(
            board, rank_a, rank_b, pairs[0], pairs[1]
        );
        EvalResult result = {
            .score = make_score(CAT_TWO_PAIR, pairs[0], pairs[1], kicker, 0, 0),
            .category = CAT_TWO_PAIR,
            .primary = (uint8_t)pairs[0],
        };
        return result;
    }

    if (pair_count >= 1) {
        EvalResult result = {
            .score = make_score(CAT_ONE_PAIR, pairs[0], singles[0], singles[1], singles[2], 0),
            .category = CAT_ONE_PAIR,
            .primary = (uint8_t)pairs[0],
        };
        return result;
    }

    EvalResult result = {
        .score = make_score(CAT_HIGH_CARD, singles[0], singles[1], singles[2], singles[3], singles[4]),
        .category = CAT_HIGH_CARD,
        .primary = (uint8_t)singles[0],
    };
    return result;
}

static inline double blind_payout(EvalResult hero_eval) {
    switch (hero_eval.category) {
        case CAT_STRAIGHT:
            return 1.0;
        case CAT_FLUSH:
            return 1.5;
        case CAT_FULL_HOUSE:
            return 3.0;
        case CAT_QUADS:
            return 10.0;
        case CAT_STRAIGHT_FLUSH:
            return hero_eval.primary == 12 ? 500.0 : 50.0;
        default:
            return 0.0;
    }
}

static inline uint8_t dealer_qualifies(EvalResult dealer_eval) {
    return dealer_eval.category >= CAT_ONE_PAIR;
}

static inline void board_state_init(BoardState *board, const uint8_t cards[5]) {
    memset(board, 0, sizeof(*board));
    for (int i = 0; i < 5; ++i) {
        const uint8_t card = cards[i];
        const int rank = card_rank[card];
        const int suit = card_suit[card];
        ++board->rank_counts[rank];
        ++board->suit_counts[suit];
        board->rank_mask |= (uint16_t)(1u << rank);
        board->suit_rank_masks[suit] |= (uint16_t)(1u << rank);
    }
}

static inline uint8_t rank_char_to_int(char c) {
    switch (c) {
        case '2': return 0;
        case '3': return 1;
        case '4': return 2;
        case '5': return 3;
        case '6': return 4;
        case '7': return 5;
        case '8': return 6;
        case '9': return 7;
        case 'T': return 8;
        case 'J': return 9;
        case 'Q': return 10;
        case 'K': return 11;
        case 'A': return 12;
        default:
            fprintf(stderr, "invalid rank: %c\n", c);
            exit(1);
    }
}

static inline uint8_t suit_char_to_int(char c) {
    switch (c) {
        case 'c': return 0;
        case 'd': return 1;
        case 'h': return 2;
        case 's': return 3;
        default:
            fprintf(stderr, "invalid suit: %c\n", c);
            exit(1);
    }
}

static inline uint8_t parse_card(const char *text) {
    if (!text || strlen(text) != 2) {
        fprintf(stderr, "invalid card string\n");
        exit(1);
    }
    const uint8_t rank = rank_char_to_int(text[0]);
    const uint8_t suit = suit_char_to_int(text[1]);
    return (uint8_t)(rank * 4 + suit);
}

static inline int popcount_u64(uint64_t x) {
    return __builtin_popcountll(x);
}

static void assert_unique_card_list(const uint8_t *cards, int count, const char *label) {
    uint64_t seen = 0;
    for (int i = 0; i < count; ++i) {
        const uint64_t bit = card_bit[cards[i]];
        if (seen & bit) {
            fprintf(stderr, "duplicate card found in %s\n", label);
            exit(1);
        }
        seen |= bit;
    }
}

static void reset_solver_state(void) {
    dead_mask = 0;
    memset(hero_cards, 0, sizeof(hero_cards));
    memset(exposed_cards, 0, sizeof(exposed_cards));
    memset(live_cards, 0, sizeof(live_cards));
    memset(board_cache, 0, BOARD_CACHE_SIZE * sizeof(*board_cache));
    memset(flop_cache, 0, FLOP_CACHE_SIZE * sizeof(*flop_cache));
    audit_board_cache_misses = 0;
    audit_board_cache_hits = 0;
    audit_board_call_count = 0;
    audit_flop_count = 0;
    audit_ordered_turn_river_count = 0;
    audit_total_dealer_worlds = 0;
}

static uint64_t mask_from_cards(const uint8_t *cards, int count) {
    uint64_t mask = 0;
    for (int i = 0; i < count; ++i) {
        mask |= card_bit[cards[i]];
    }
    return mask;
}

static BoardCacheEntry *board_cache_find(uint64_t key) {
    uint32_t slot = (uint32_t)(mix_u64(key) & (BOARD_CACHE_SIZE - 1));
    while (board_cache[slot].key != 0 && board_cache[slot].key != key) {
        slot = (slot + 1u) & (BOARD_CACHE_SIZE - 1);
    }
    return &board_cache[slot];
}

static FlopCacheEntry *flop_cache_find(uint64_t key) {
    uint32_t slot = (uint32_t)(mix_u64(key) & (FLOP_CACHE_SIZE - 1));
    while (flop_cache[slot].key != 0 && flop_cache[slot].key != key) {
        slot = (slot + 1u) & (FLOP_CACHE_SIZE - 1);
    }
    return &flop_cache[slot];
}

static BoardCacheEntry *solve_board(uint64_t board_mask, const uint8_t board_cards[5]) {
    ++audit_board_call_count;
    BoardCacheEntry *entry = board_cache_find(board_mask);
    if (entry->key == board_mask) {
        if (audit_enabled) {
            ++audit_board_cache_hits;
        }
        return entry;
    }
    if (audit_enabled) {
        ++audit_board_cache_misses;
        if (popcount_u64(board_mask) != 5) {
            fprintf(stderr, "audit failure: board mask does not contain 5 cards\n");
            exit(1);
        }
        if (board_mask & dead_mask) {
            fprintf(stderr, "audit failure: board overlaps dead cards\n");
            exit(1);
        }
        assert_unique_card_list(board_cards, 5, "solve_board board_cards");
        if (mask_from_cards(board_cards, 5) != board_mask) {
            fprintf(stderr, "audit failure: board mask mismatch\n");
            exit(1);
        }
    }

    BoardState board_state;
    board_state_init(&board_state, board_cards);
    const EvalResult hero_eval = eval_with_two(&board_state, hero_cards[0], hero_cards[1]);
    const double blind_win = blind_payout(hero_eval);

    double total1 = 0.0;
    double total2 = 0.0;
    double total4 = 0.0;
    int dealer_worlds = 0;

    for (int i = 0; i < PAIR_COUNT; ++i) {
        const PairIdx pair = pair_indices[i];
        if (pair.mask & board_mask) {
            continue;
        }
        ++dealer_worlds;

        const EvalResult dealer_eval =
            eval_with_two(&board_state, live_cards[pair.a], live_cards[pair.b]);

        if (hero_eval.score > dealer_eval.score) {
            const double ante = dealer_qualifies(dealer_eval) ? 1.0 : 0.0;
            const double base = ante + blind_win;
            total1 += base + 1.0;
            total2 += base + 2.0;
            total4 += base + 4.0;
        } else if (hero_eval.score < dealer_eval.score) {
            const double ante = dealer_qualifies(dealer_eval) ? -1.0 : 0.0;
            const double base = ante - 1.0;
            total1 += base - 1.0;
            total2 += base - 2.0;
            total4 += base - 4.0;
        }
    }
    if (audit_enabled) {
        if (dealer_worlds != DEALER_COMBOS_PER_BOARD) {
            fprintf(
                stderr,
                "audit failure: expected %d dealer worlds, found %d\n",
                DEALER_COMBOS_PER_BOARD,
                dealer_worlds
            );
            exit(1);
        }
        audit_total_dealer_worlds += (uint64_t)dealer_worlds;
    }

    entry->key = board_mask;
    entry->ev1 = total1 / (double)DEALER_COMBOS_PER_BOARD;
    entry->ev2 = total2 / (double)DEALER_COMBOS_PER_BOARD;
    entry->ev4 = total4 / (double)DEALER_COMBOS_PER_BOARD;
    return entry;
}

static inline double solve_river(uint64_t board_mask, const uint8_t board_cards[5]) {
    const BoardCacheEntry *board = solve_board(board_mask, board_cards);
    return board->ev1 >= FOLD_EV ? board->ev1 : FOLD_EV;
}

static FlopCacheEntry *solve_flop(uint64_t flop_mask, const uint8_t flop_cards[3]) {
    FlopCacheEntry *entry = flop_cache_find(flop_mask);
    if (entry->key == flop_mask) {
        return entry;
    }
    if (audit_enabled) {
        ++audit_flop_count;
        if (popcount_u64(flop_mask) != 3) {
            fprintf(stderr, "audit failure: flop mask does not contain 3 cards\n");
            exit(1);
        }
        if (flop_mask & dead_mask) {
            fprintf(stderr, "audit failure: flop overlaps dead cards\n");
            exit(1);
        }
        assert_unique_card_list(flop_cards, 3, "solve_flop flop_cards");
        if (mask_from_cards(flop_cards, 3) != flop_mask) {
            fprintf(stderr, "audit failure: flop mask mismatch\n");
            exit(1);
        }
    }

    uint8_t remaining[37];
    int remaining_count = 0;
    for (int i = 0; i < LIVE_CARD_COUNT; ++i) {
        if ((card_bit[live_cards[i]] & flop_mask) == 0) {
            remaining[remaining_count++] = live_cards[i];
        }
    }

    double total4 = 0.0;
    double total2 = 0.0;
    double total_check = 0.0;

    uint8_t board_cards[5];
    board_cards[0] = flop_cards[0];
    board_cards[1] = flop_cards[1];
    board_cards[2] = flop_cards[2];

    for (int ti = 0; ti < remaining_count; ++ti) {
        board_cards[3] = remaining[ti];
        for (int ri = 0; ri < remaining_count; ++ri) {
            if (ri == ti) {
                continue;
            }
            board_cards[4] = remaining[ri];
            const uint64_t board_mask =
                flop_mask |
                card_bit[remaining[ti]] |
                card_bit[remaining[ri]];
            const BoardCacheEntry *board = solve_board(board_mask, board_cards);
            total4 += board->ev4;
            total2 += board->ev2;
            total_check += solve_river(board_mask, board_cards);
            if (audit_enabled) {
                ++audit_ordered_turn_river_count;
            }
        }
    }
    if (audit_enabled && remaining_count != 37) {
        fprintf(stderr, "audit failure: expected 37 remaining cards, found %d\n", remaining_count);
        exit(1);
    }
    if (audit_enabled && (total4 != total4 || total2 != total2 || total_check != total_check)) {
        fprintf(stderr, "audit failure: NaN encountered in flop accumulation\n");
        exit(1);
    }

    const double denom = 37.0 * 36.0;
    entry->key = flop_mask;
    entry->ev4 = total4 / denom;
    entry->ev2 = total2 / denom;
    entry->ev_check = total_check / denom;
    entry->ev = entry->ev2 >= entry->ev_check ? entry->ev2 : entry->ev_check;
    return entry;
}

typedef struct {
    double ev4;
    double ev_check;
    double ev_best;
} PreflopResult;

static PreflopResult solve_preflop(void) {
    double total4 = 0.0;
    double total_check = 0.0;

    uint8_t flop_cards[3];
    for (int i = 0; i < FLOP_COUNT; ++i) {
        const TripleIdx flop = flop_indices[i];
        flop_cards[0] = live_cards[flop.a];
        flop_cards[1] = live_cards[flop.b];
        flop_cards[2] = live_cards[flop.c];
        const FlopCacheEntry *entry = solve_flop(flop.mask, flop_cards);
        total4 += entry->ev4;
        total_check += entry->ev;
    }

    PreflopResult result;
    result.ev4 = total4 / (double)FLOP_COUNT;
    result.ev_check = total_check / (double)FLOP_COUNT;
    result.ev_best = result.ev4 >= result.ev_check ? result.ev4 : result.ev_check;
    return result;
}

static void print_audit_summary(void) {
    printf("Audit:\n");
    printf("  live_cards                 = %d\n", LIVE_CARD_COUNT);
    printf("  flops_visited              = %llu\n", (unsigned long long)audit_flop_count);
    printf("  solve_board_calls          = %llu\n", (unsigned long long)audit_board_call_count);
    printf("  ordered_turn_river_paths   = %llu\n", (unsigned long long)audit_ordered_turn_river_count);
    printf("  unique_completed_boards    = %llu\n", (unsigned long long)audit_board_cache_misses);
    printf("  completed_board_cache_hits = %llu\n", (unsigned long long)audit_board_cache_hits);
    printf("  total_dealer_worlds        = %llu\n", (unsigned long long)audit_total_dealer_worlds);

    if (audit_flop_count != FLOP_COUNT) {
        fprintf(stderr, "audit failure: expected %d flops, found %llu\n", FLOP_COUNT, (unsigned long long)audit_flop_count);
        exit(1);
    }
    if (audit_ordered_turn_river_count != (uint64_t)FLOP_COUNT * (uint64_t)ORDERED_TURN_RIVER_COUNT) {
        fprintf(
            stderr,
            "audit failure: expected %llu ordered turn/river paths, found %llu\n",
            (unsigned long long)((uint64_t)FLOP_COUNT * (uint64_t)ORDERED_TURN_RIVER_COUNT),
            (unsigned long long)audit_ordered_turn_river_count
        );
        exit(1);
    }
    if (audit_board_cache_misses != TOTAL_BOARD_COUNT) {
        fprintf(
            stderr,
            "audit failure: expected %d unique completed boards, found %llu\n",
            TOTAL_BOARD_COUNT,
            (unsigned long long)audit_board_cache_misses
        );
        exit(1);
    }
    if (audit_total_dealer_worlds != (uint64_t)TOTAL_BOARD_COUNT * (uint64_t)DEALER_COMBOS_PER_BOARD) {
        fprintf(
            stderr,
            "audit failure: expected %llu dealer worlds, found %llu\n",
            (unsigned long long)((uint64_t)TOTAL_BOARD_COUNT * (uint64_t)DEALER_COMBOS_PER_BOARD),
            (unsigned long long)audit_total_dealer_worlds
        );
        exit(1);
    }
}

static void init_cards(const char *hero_inputs[2], const char *exposed_inputs[10]) {
    reset_solver_state();
    for (int rank = 0; rank < 13; ++rank) {
        for (int suit = 0; suit < 4; ++suit) {
            const int card = rank * 4 + suit;
            card_rank[card] = (uint8_t)rank;
            card_suit[card] = (uint8_t)suit;
            card_bit[card] = 1ULL << card;
        }
    }

    for (int i = 0; i < 2; ++i) {
        hero_cards[i] = parse_card(hero_inputs[i]);
        dead_mask |= card_bit[hero_cards[i]];
    }
    for (int i = 0; i < 10; ++i) {
        exposed_cards[i] = parse_card(exposed_inputs[i]);
        dead_mask |= card_bit[exposed_cards[i]];
    }

    uint8_t all_inputs[12];
    all_inputs[0] = hero_cards[0];
    all_inputs[1] = hero_cards[1];
    for (int i = 0; i < 10; ++i) {
        all_inputs[i + 2] = exposed_cards[i];
    }
    assert_unique_card_list(all_inputs, 12, "hero/exposed inputs");

    int live_count = 0;
    for (int card = 0; card < 52; ++card) {
        if ((dead_mask & card_bit[card]) == 0) {
            live_cards[live_count++] = (uint8_t)card;
        }
    }
    if (live_count != LIVE_CARD_COUNT) {
        fprintf(stderr, "expected %d live cards, found %d\n", LIVE_CARD_COUNT, live_count);
        exit(1);
    }
}

static void precompute_pairs(void) {
    int index = 0;
    for (int i = 0; i < LIVE_CARD_COUNT; ++i) {
        for (int j = i + 1; j < LIVE_CARD_COUNT; ++j) {
            pair_indices[index].a = (uint8_t)i;
            pair_indices[index].b = (uint8_t)j;
            pair_indices[index].mask = card_bit[live_cards[i]] | card_bit[live_cards[j]];
            ++index;
        }
    }
    if (index != PAIR_COUNT) {
        fprintf(stderr, "pair precompute mismatch\n");
        exit(1);
    }
}

static void precompute_flops(void) {
    int index = 0;
    for (int i = 0; i < LIVE_CARD_COUNT; ++i) {
        for (int j = i + 1; j < LIVE_CARD_COUNT; ++j) {
            for (int k = j + 1; k < LIVE_CARD_COUNT; ++k) {
                flop_indices[index].a = (uint8_t)i;
                flop_indices[index].b = (uint8_t)j;
                flop_indices[index].c = (uint8_t)k;
                flop_indices[index].mask =
                    card_bit[live_cards[i]] |
                    card_bit[live_cards[j]] |
                    card_bit[live_cards[k]];
                ++index;
            }
        }
    }
    if (index != FLOP_COUNT) {
        fprintf(stderr, "flop precompute mismatch\n");
        exit(1);
    }
}

static void init_caches(void) {
    board_cache = calloc(BOARD_CACHE_SIZE, sizeof(*board_cache));
    flop_cache = calloc(FLOP_CACHE_SIZE, sizeof(*flop_cache));
    if (!board_cache || !flop_cache) {
        fprintf(stderr, "cache allocation failed\n");
        exit(1);
    }
}

static void run_board_mode(const char *board_inputs[5]) {
    uint8_t board_cards[5];
    for (int i = 0; i < 5; ++i) {
        board_cards[i] = parse_card(board_inputs[i]);
    }
    assert_unique_card_list(board_cards, 5, "board cards");
    const uint64_t board_mask = mask_from_cards(board_cards, 5);
    if (board_mask & dead_mask) {
        fprintf(stderr, "board cards overlap hero/exposed cards\n");
        exit(1);
    }
    const BoardCacheEntry *board = solve_board(board_mask, board_cards);
    printf(
        "Board EVs for %s %s %s %s %s: ev1=%.9f ev2=%.9f ev4=%.9f\n",
        board_inputs[0], board_inputs[1], board_inputs[2], board_inputs[3], board_inputs[4],
        board->ev1, board->ev2, board->ev4
    );
}

static void run_flop_mode(const char *flop_inputs[3]) {
    uint8_t flop_cards[3];
    for (int i = 0; i < 3; ++i) {
        flop_cards[i] = parse_card(flop_inputs[i]);
    }
    assert_unique_card_list(flop_cards, 3, "flop cards");
    const uint64_t flop_mask = mask_from_cards(flop_cards, 3);
    if (flop_mask & dead_mask) {
        fprintf(stderr, "flop cards overlap hero/exposed cards\n");
        exit(1);
    }
    const FlopCacheEntry *flop = solve_flop(flop_mask, flop_cards);
    printf(
        "Flop EVs for %s %s %s: ev4=%.9f ev2=%.9f ev_check=%.9f ev=%.9f\n",
        flop_inputs[0], flop_inputs[1], flop_inputs[2],
        flop->ev4, flop->ev2, flop->ev_check, flop->ev
    );
}

static void run_eval7_mode(const char *cards_in[7]) {
    uint8_t cards[7];
    for (int i = 0; i < 7; ++i) {
        cards[i] = parse_card(cards_in[i]);
    }
    assert_unique_card_list(cards, 7, "eval7 cards");
    BoardState board_state;
    board_state_init(&board_state, cards);
    const EvalResult eval = eval_with_two(&board_state, cards[5], cards[6]);
    printf(
        "Eval7 for %s %s %s %s %s %s %s: score=%u category=%u primary=%u\n",
        cards_in[0], cards_in[1], cards_in[2], cards_in[3], cards_in[4], cards_in[5], cards_in[6],
        eval.score, eval.category, eval.primary
    );
}

int main(int argc, char **argv) {
    init_caches();

    if (argc == 7 && strcmp(argv[1], "--board-ev") == 0) {
        const char *hero_inputs[2] = {DEFAULT_HERO_STRS[0], DEFAULT_HERO_STRS[1]};
        const char *exposed_inputs[10] = {
            DEFAULT_EXPOSED_STRS[0], DEFAULT_EXPOSED_STRS[1], DEFAULT_EXPOSED_STRS[2],
            DEFAULT_EXPOSED_STRS[3], DEFAULT_EXPOSED_STRS[4], DEFAULT_EXPOSED_STRS[5],
            DEFAULT_EXPOSED_STRS[6], DEFAULT_EXPOSED_STRS[7], DEFAULT_EXPOSED_STRS[8],
            DEFAULT_EXPOSED_STRS[9]
        };
        init_cards(hero_inputs, exposed_inputs);
        precompute_pairs();
        run_board_mode((const char **)&argv[2]);
        free(board_cache);
        free(flop_cache);
        return 0;
    }

    if (argc == 5 && strcmp(argv[1], "--flop-ev") == 0) {
        const char *hero_inputs[2] = {DEFAULT_HERO_STRS[0], DEFAULT_HERO_STRS[1]};
        const char *exposed_inputs[10] = {
            DEFAULT_EXPOSED_STRS[0], DEFAULT_EXPOSED_STRS[1], DEFAULT_EXPOSED_STRS[2],
            DEFAULT_EXPOSED_STRS[3], DEFAULT_EXPOSED_STRS[4], DEFAULT_EXPOSED_STRS[5],
            DEFAULT_EXPOSED_STRS[6], DEFAULT_EXPOSED_STRS[7], DEFAULT_EXPOSED_STRS[8],
            DEFAULT_EXPOSED_STRS[9]
        };
        init_cards(hero_inputs, exposed_inputs);
        precompute_pairs();
        run_flop_mode((const char **)&argv[2]);
        free(board_cache);
        free(flop_cache);
        return 0;
    }

    if (argc == 9 && strcmp(argv[1], "--eval7") == 0) {
        for (int rank = 0; rank < 13; ++rank) {
            for (int suit = 0; suit < 4; ++suit) {
                const int card = rank * 4 + suit;
                card_rank[card] = (uint8_t)rank;
                card_suit[card] = (uint8_t)suit;
                card_bit[card] = 1ULL << card;
            }
        }
        run_eval7_mode((const char **)&argv[2]);
        free(board_cache);
        free(flop_cache);
        return 0;
    }

    const char *hero_inputs[2] = {DEFAULT_HERO_STRS[0], DEFAULT_HERO_STRS[1]};
    const char *exposed_inputs[10] = {
        DEFAULT_EXPOSED_STRS[0], DEFAULT_EXPOSED_STRS[1], DEFAULT_EXPOSED_STRS[2],
        DEFAULT_EXPOSED_STRS[3], DEFAULT_EXPOSED_STRS[4], DEFAULT_EXPOSED_STRS[5],
        DEFAULT_EXPOSED_STRS[6], DEFAULT_EXPOSED_STRS[7], DEFAULT_EXPOSED_STRS[8],
        DEFAULT_EXPOSED_STRS[9]
    };
    int arg_offset = 0;
    if (argc >= 2 && strcmp(argv[1], "--audit") == 0) {
        audit_enabled = 1;
        arg_offset = 1;
    }

    if (argc == 13 + arg_offset) {
        hero_inputs[0] = argv[1 + arg_offset];
        hero_inputs[1] = argv[2 + arg_offset];
        for (int i = 0; i < 10; ++i) {
            exposed_inputs[i] = argv[i + 3 + arg_offset];
        }
    } else if (argc != 1 + arg_offset) {
        fprintf(
            stderr,
            "usage: %s [--audit] [hero1 hero2 exposed1 exposed2 exposed3 exposed4 exposed5 exposed6 exposed7 exposed8 exposed9 exposed10]\n",
            argv[0]
        );
        free(board_cache);
        free(flop_cache);
        return 1;
    }

    for (int i = 0; i < 2; ++i) {
        snprintf(hero_text[i], sizeof(hero_text[i]), "%s", hero_inputs[i]);
    }
    for (int i = 0; i < 10; ++i) {
        snprintf(exposed_text[i], sizeof(exposed_text[i]), "%s", exposed_inputs[i]);
    }

    init_cards(hero_inputs, exposed_inputs);
    precompute_pairs();
    precompute_flops();

    const double start = monotonic_seconds();
    const PreflopResult result = solve_preflop();
    const double elapsed = monotonic_seconds() - start;

    printf("Hero: %s %s\n", hero_text[0], hero_text[1]);
    printf(
        "Exposed: %s %s %s %s %s %s %s %s %s %s\n",
        exposed_text[0], exposed_text[1], exposed_text[2], exposed_text[3], exposed_text[4],
        exposed_text[5], exposed_text[6], exposed_text[7], exposed_text[8], exposed_text[9]
    );
    printf("EV(4x)    = %.9f\n", result.ev4);
    printf("EV(check) = %.9f\n", result.ev_check);
    printf("Best EV   = %.9f\n", result.ev_best);
    printf("Elapsed   = %.3f s\n", elapsed);
    if (audit_enabled) {
        print_audit_summary();
    }

    free(board_cache);
    free(flop_cache);
    return 0;
}
