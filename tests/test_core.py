import unittest
from src.models import Account, Post, ScrapeLog
from src.db import Database
from src.ingest import ingest_scraped_batch
from src.tools import search_scraped_posts, get_engagement_summary, compare_accounts, execute_claude_tool
from src.api import handle_get_accounts, handle_post_accounts, handle_get_posts, handle_get_posts_summary, handle_chat_message


class TestCore(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.acc_ig = Account.create(platform="instagram", username="easylegal_id", is_own_brand=True)
        self.acc_tt = Account.create(platform="tiktok", username="easylegal_tiktok", is_own_brand=True)
        self.acc_comp = Account.create(platform="instagram", username="competitor_legal", is_own_brand=False)

        self.db.upsert_account(self.acc_ig)
        self.db.upsert_account(self.acc_tt)
        self.db.upsert_account(self.acc_comp)

    def tearDown(self):
        self.db.close()

    def test_account_creation_and_listing(self):
        accounts = self.db.list_accounts()
        self.assertEqual(len(accounts), 3)

    def test_ingest_instagram_posts(self):
        raw_ig_posts = [
            {
                "shortcode": "C12345",
                "caption": "Tips mendirikan PT PMA di Indonesia #EasyLegal #Bisnis",
                "display_url": "https://img.instagram.com/p/1.jpg",
                "likes": 150,
                "comments": 25,
                "video_view_count": None,
                "date_utc": "2026-01-10T10:00:00Z",
            },
            {
                "shortcode": "C12346",
                "caption": "Promo pendaftaran merek dagang HKI bulan ini",
                "display_url": "https://img.instagram.com/p/2.jpg",
                "likes": 320,
                "comments": 40,
                "video_view_count": None,
                "date_utc": "2026-01-15T12:00:00Z",
            },
        ]
        inserted, err = ingest_scraped_batch(self.db, "instagram", self.acc_ig, raw_ig_posts)
        self.assertIsNone(err)
        self.assertEqual(inserted, 2)

        posts = self.db.query_posts(account_id=self.acc_ig.id)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["likes"], 320)  # Ordered by posted_at DESC

    def test_ingest_tiktok_posts(self):
        raw_tt_posts = [
            {
                "id": "7891011",
                "desc": "Cara cek legalitas usaha online #tiktoklegal #tipsbisnis",
                "video": {"downloadAddr": "https://v.tiktok.com/vid1.mp4"},
                "stats": {"diggCount": 1200, "commentCount": 85, "playCount": 15000},
                "createTime": 1768000000,
            }
        ]
        inserted, err = ingest_scraped_batch(self.db, "tiktok", self.acc_tt, raw_tt_posts)
        self.assertIsNone(err)
        self.assertEqual(inserted, 1)

        summary = self.db.get_account_summary(self.acc_tt.id)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total_likes"], 1200)
        self.assertEqual(summary["total_views"], 15000)

    def test_claude_tools(self):
        # Ingest test posts
        posts = [
            {
                "shortcode": f"POST_{i}",
                "caption": f"Legal advice topic {i} tentang perizinan usaha",
                "display_url": f"https://img.com/{i}.jpg",
                "likes": 100 * i,
                "comments": 10 * i,
                "date_utc": f"2026-01-{i:02d}T00:00:00Z",
            }
            for i in range(1, 6)
        ]
        ingest_scraped_batch(self.db, "instagram", self.acc_ig, posts)

        # 1. search_scraped_posts
        search_res = search_scraped_posts(self.db, keyword="perizinan", limit=10)
        self.assertEqual(search_res["status"], "success")
        self.assertEqual(search_res["count"], 5)

        # 2. get_engagement_summary
        summary_res = get_engagement_summary(self.db, "easylegal_id", "instagram")
        self.assertEqual(summary_res["status"], "success")
        self.assertEqual(summary_res["summary"]["total_posts"], 5)
        self.assertEqual(summary_res["summary"]["total_likes"], 1500)
        self.assertEqual(summary_res["summary"]["avg_likes"], 300.0)

        # 3. compare_accounts
        comp_res = compare_accounts(self.db, ["easylegal_id", "competitor_legal"])
        self.assertEqual(comp_res["status"], "success")
        self.assertEqual(len(comp_res["accounts"]), 2)

    def test_api_and_chat(self):
        # GET /accounts
        res_acc = handle_get_accounts(self.db)
        self.assertEqual(res_acc["status"], "success")
        self.assertEqual(res_acc["count"], 3)

        # POST /accounts
        new_acc = handle_post_accounts(self.db, {"platform": "instagram", "username": "easytax_id", "is_own_brand": True})
        self.assertEqual(new_acc["status"], "success")

        # Chat intent: engagement
        chat_res = handle_chat_message(self.db, "berapa engagement rata-rata akun easylegal_id bulan ini?")
        self.assertEqual(chat_res["status"], "success")
        self.assertEqual(chat_res["tool_used"], "get_engagement_summary")

    def test_post_url_round_trips_through_upsert_and_query(self):
        post = Post.create(
            account_id=self.acc_ig.id,
            platform_post_id="C99999",
            caption="Post dengan permalink",
            media_url="https://img.instagram.com/p/99.jpg",
            likes=10,
            comments=1,
            platform="instagram",
            post_url="https://www.instagram.com/p/C99999/",
        )
        self.db.upsert_posts([post])
        stored = self.db.query_posts(account_id=self.acc_ig.id, limit=5)
        self.assertEqual(stored[0]["post_url"], "https://www.instagram.com/p/C99999/")

    def test_rescraping_same_post_updates_permalink_not_duplicates_row(self):
        first = Post.create(
            account_id=self.acc_ig.id, platform_post_id="C1", caption="v1",
            media_url="", likes=1, comments=0, platform="instagram", post_url="",
        )
        self.db.upsert_posts([first])
        second = Post.create(
            account_id=self.acc_ig.id, platform_post_id="C1", caption="v1",
            media_url="", likes=5, comments=2, platform="instagram",
            post_url="https://www.instagram.com/p/C1/",
        )
        self.db.upsert_posts([second])

        stored = self.db.query_posts(account_id=self.acc_ig.id, limit=10)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["likes"], 5)
        self.assertEqual(stored[0]["post_url"], "https://www.instagram.com/p/C1/")

    def test_update_account_follower_count_persists_and_is_readable(self):
        updated = self.db.update_account_follower_count(self.acc_ig.id, 42000)
        self.assertEqual(updated.follower_count, 42000)
        reloaded = self.db.get_account(self.acc_ig.id)
        self.assertEqual(reloaded.follower_count, 42000)

    def test_update_account_follower_count_none_is_a_no_op(self):
        self.db.update_account_follower_count(self.acc_ig.id, 42000)
        result = self.db.update_account_follower_count(self.acc_ig.id, None)
        self.assertEqual(result.follower_count, 42000)


if __name__ == "__main__":
    unittest.main()
