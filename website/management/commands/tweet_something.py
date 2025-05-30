from django.db.models import F
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.template.defaultfilters import truncatechars

from collections import defaultdict
import json, os, sys
from datetime import timedelta

class OkITweetedSomething(Exception):
	pass

class Command(BaseCommand):
	help = 'Post a tracked legislative event to social media.'

	tweets_storage_fn = 'data/misc/tweets.json'

	def add_arguments(self, parser):
		parser.add_argument('--ignore-history', action="store_true")
		parser.add_argument('--dry-run', action="store_true")
		parser.add_argument('--max', action="store", type=int, default=1)

	def handle(self, *args, **options):
		self.options = options

		# For testing, how many tweets to do in this run.
		self.max = self.options["max"]

		# Construct clients.
		#from website.util import twitter_api_client
		#self.tweepy_client = twitter_api_client()

		from mastodon import Mastodon
		self.mastodon = Mastodon(
		    access_token = settings.MASTODON_GOVTRACK_PUSH_BOT_ACCESS_TOKEN,
		    api_base_url = 'https://mastodon.social'
		)

		from atproto import Client
		self.bluesky = Client()
		self.bluesky.login(settings.BLUESKY_USERNAME, settings.BLUESKY_PASSWORD)

		if options["ignore_history"]:
			self.previous_tweets = { }

		else:
			# What have we tweeted about before? Let's not tweet
			# it again.
			self.load_previous_tweets()

		try:
			# Send out a tweet.
			self.tweet_something()

		except OkITweetedSomething:
			pass

		finally:
			# Don't update history if we didn't actually tweet or didn't load history.
			if options["ignore_history"] or options["dry_run"]:
				return

			# Save the updated cache of previous tweets for next time.
			self.save_previous_tweets()

	def load_previous_tweets(self):
		if not os.path.exists(self.tweets_storage_fn):
			self.previous_tweets =  { }
		else:
			self.previous_tweets =  json.loads(open(self.tweets_storage_fn).read())

	def save_previous_tweets(self):
		def myconverter(o): # Mastodon returns these
			import datetime
			if isinstance(o, datetime.datetime):
				return o.__str__()

		with open(self.tweets_storage_fn, 'w') as output:
			json.dump(self.previous_tweets, output, sort_keys=True, indent=2, default=myconverter)

	###

	def tweet_something(self):
		# Find something interesting to tweet!
		self.tweet_new_signed_laws_yday()
		self.tweet_votes_yday(True)
		self.tweet_missing_legislators()
		self.tweet_new_bills_yday()
		self.tweet_coming_up()
		self.tweet_a_bill_action()
		self.tweet_votes_yday(False)
		#self.test_message()

	###

	def post_tweet(self, key, text, url):
		if key in self.previous_tweets:
			return

		# For good measure, ensure Unicode is normalized. Twitter
		# counts characters on normalized strings.
		import unicodedata
		text = unicodedata.normalize('NFC', text)

		if self.options["dry_run"]:
			# Don't tweet. Just print and exit.
			print(text)
			self.max -= 1
			if self.max <= 0:
				sys.exit(1)
			return

		# Truncate to hit the right total length.

		toot_text = truncatechars(text,
			480 # max toot length, minus some amount in case Unicode is handled weirdly in the character count limit
			-1 # space
			-23 # links all count as 23 characters per https://docs.joinmastodon.org/user/posting/
			-1 # space
			-4 # emoji
		)
		toot_text += " "
		toot_text += url
		toot_text += " 🏛️" # there's a civics building emoji there indicating to followers this is an automated tweet? the emoji is four(?) characters as Twitter sees it (plus the preceding space)

		# Toot

		try:
			toot = self.mastodon.toot(toot_text)
		except Exception as e:
			toot = { "error": str(e) }

		# Now Bluesky

		bsky_text = truncatechars(text,
			300 # max length measured in grapheme clusters, so we may get a few extra codepoints
			-1 # space
			-(len(url) * 2 + 4) # seems like it might be markdown-encoded internally, didn't check
			-1 # space
			-4 # emoji
		)
		from atproto import client_utils as atproto_client_utils
		bsky_text = atproto_client_utils.TextBuilder().text(bsky_text)
		bsky_text.text(" ")
		bsky_text.link(url, url)
		bsky_text.text(" 🏛️") # there's a civics building emoji there indicating to followers this is an automated tweet? the emoji is four(?) characters as Twitter sees it (plus the preceding space)

		try:
			bskypost = self.bluesky.send_post(bsky_text)
		except Exception as e:
			bskypost = { "error": str(e) }

		self.previous_tweets[key] = {
			"text": text,
			"when": timezone.now().isoformat(),
			"toot": toot,
			"bsky": bskypost,
		}

		#print(json.dumps(self.previous_tweets[key], indent=2))

		raise OkITweetedSomething()

	###

	def tweet_new_signed_laws_yday(self):
		# Because of possible data delays, don't tweet until the afternoon.
		if timezone.now().hour < 12: return

		# Tweet count of new laws enacted yesterday.
		from bill.models import Bill, BillStatus
		count = Bill.objects.filter(
			current_status_date__gte=timezone.now().date()-timedelta(days=1),
			current_status_date__lt=timezone.now().date(),
			current_status=BillStatus.enacted_signed,
		).count()
		if count == 0: return
		self.post_tweet(
			"%s:newlaws" % timezone.now().date().isoformat(),
			"%d new law%s signed by the President yesterday." % (
				count,
				"s were" if count != 1 else " was",
				),
			"https://www.govtrack.us/congress/bills/browse#current_status[]=28&sort=-current_status_date&congress=__ALL__")
		# Since laws can be enacted after the end of a Congress, we can't generate a link that is for
		# bills only in the current Congress.

	def tweet_votes_yday(self, if_major):
		# Tweet count of votes yesterday, by vote type if there were any major votes.
		from vote.models import Vote, VoteCategory

		votes = Vote.objects.filter(
			created__gte=timezone.now().date()-timedelta(days=1),
			created__lt=timezone.now().date(),
		)
		if votes.count() == 0: return

		has_major = len([v for v in votes if v.is_major]) > 0
		if not has_major and if_major: return

		if not has_major:
			count = votes.count()
			msg = "%d minor vote%s held by Congress yesterday." % (
                count,
                "s were" if count != 1 else " was",
                )
		else:
			counts = defaultdict(lambda : 0)
			for v in votes:
				counts[v.category] += 1
			counts = list(counts.items())
			counts.sort(key = lambda kv : (VoteCategory.by_value(kv[0]).importance, -kv[1]))
			msg = "Votes held by Congress yesterday: " + ", ".join(
				str(value) + " on " + VoteCategory.by_value(key).label
				for key, value in counts
			)

		self.post_tweet(
			"%s:votes" % timezone.now().date().isoformat(),
			msg,
			"https://www.govtrack.us/congress/votes")

	def tweet_new_bills_yday(self):
		# Because of possible data delays, don't tweet until the afternoon.
		if timezone.now().hour < 12: return

		# Tweet count of new bills introduced yesterday.
		from bill.models import Bill, BillStatus
		count = Bill.objects.filter(
			introduced_date__gte=timezone.now().date()-timedelta(days=1),
			introduced_date__lt=timezone.now().date(),
		).count()
		if count == 0: return
		self.post_tweet(
			"%s:newbills" % timezone.now().date().isoformat(),
			"%d bill%s introduced in Congress yesterday." % (
				count,
				"s were" if count != 1 else " was",
				),
			"https://www.govtrack.us/congress/bills/browse#sort=-introduced_date")

	def tweet_coming_up(self):
        # legislation posted as coming up within the last day
		from bill.models import Bill
		dhg_bills = Bill.objects.filter(docs_house_gov_postdate__gt=timezone.now().date()-timedelta(days=1)).filter(docs_house_gov_postdate__gt=F('current_status_date'))
		sfs_bills = Bill.objects.filter(senate_floor_schedule_postdate__gt=timezone.now().date()-timedelta(days=1)).filter(senate_floor_schedule_postdate__gt=F('current_status_date'))
		coming_up = list(dhg_bills | sfs_bills)
		coming_up.sort(key = lambda b : b.docs_house_gov_postdate if (b.docs_house_gov_postdate and (not b.senate_floor_schedule_postdate or b.senate_floor_schedule_postdate < b.docs_house_gov_postdate)) else b.senate_floor_schedule_postdate)
		for bill in coming_up:
			text = "\U0001f51c " + bill.display_number # SOON-> emoji
			text += Command.mention_sponsors(bill)
			text += ": " + bill.title_no_number
			self.post_tweet(
				"%s:comingup:%s" % (timezone.now().date().isoformat(), bill.congressproject_id),
				text,
				"https://www.govtrack.us" + bill.get_absolute_url())

	def tweet_a_bill_action(self):
		# Tweet an interesting action on a bill.
		from bill.models import Bill, BillStatus
		from bill.status import get_bill_really_short_status_string
		bills = list(Bill.objects.filter(
			current_status_date__gte=timezone.now().date()-timedelta(days=1),
			current_status_date__lt=timezone.now().date(),
		).exclude(
			current_status=BillStatus.introduced,
		))
		if len(bills) == 0: return

		# Expand list to bill-status-via pairs. For bills that had actions themselves, via is None.
		bills = [
			(bill, BillStatus.by_value(bill.current_status), None)
			for bill in bills
		]

		# From enacted bills, add all incorporated bills, but apply the status of
		# the enacted bill so we tweet that it also was enacted and track which
		# bill it was enacted through. Skip bills with the same title, since that
		# will appear redundant and that should cover companion bills that we
		# already pull in the sponsors name for. Expand the source set of bills
		# back more days because text analysis can take time to complete.
		for b in Bill.objects.filter(
			current_status_date__gte=timezone.now().date()-timedelta(days=20),
			current_status__in=BillStatus.final_status_enacted_bill):
			if b.text_incorporation:
				for rec in b.text_incorporation:
					if rec["my_version"] == "enr": # one side is always enr
						b2 = Bill.from_congressproject_id(rec["other"])
						if b.title_no_number == b2.title_no_number: continue # probably a companion bill, see below
						bills.append((b2, BillStatus.enacted_incorporation, b))

		# Choose bill with the most salient status, breaking ties with the highest proscore.
		bills.sort(key = lambda b : (b[2] is None, b[1].sort_order, b[0].proscore()), reverse=True)

		# Post tweets.
		for bill, status, via in bills:
			if "Providing for consideration" in bill.title: continue
			text = get_bill_really_short_status_string(status.xml_code)
			if text == "": continue
			bill_number = bill.display_number
			bill_number += Command.mention_sponsors(bill)
			text = text % (bill_number, "yesterday" if via is None else "on " + via.current_status_date.strftime("%x"))
			if via: text = text.rstrip('.') + " (via {}).".format(via.display_number)
			text += " " + bill.title_no_number
			self.post_tweet(
				bill.current_status_date.isoformat() + ":bill:%s:status:%s" % (bill.congressproject_id, status),
				text,
				"https://www.govtrack.us" + bill.get_absolute_url())

	@staticmethod
	def mention_sponsors(bill):
		sponsors = bill.get_sponsors()
		#sponsors = [p for p in sponsors if p.twitterid]
		#if not sponsors: return "" # no sponsors or none with a twitter handle
		return " by " + ", ".join(sponsor.name for sponsor in sponsors)

	def tweet_missing_legislators(self):
		if settings.CURRENT_CONGRESS < 119: return # don't flood
		from person.analysis import load_missing_legislators
		for m in load_missing_legislators(settings.CURRENT_CONGRESS):
			if m["returntotalvotes"]: continue # skip if legislator appears to already have returned
			self.post_tweet(
				"missingmember:" + str(m["person"].id) + ":" + m["firstmissedvote"].isoformat(),
				f"""{m["person"].name} may be missing. {m["person"].he_she_cap} missed {m["missedvotes"]}"""
				+ f""" of {m["totalvotes"]} roll call votes ({m["missedvotespct"]}%)"""
				+ f""" since {m["firstmissedvote"].strftime("%x")}.""",
				"https://www.govtrack.us/congress/members/missing")

	def test_message(self):
		self.post_tweet("testmessage", "This is a test of our automated legislative update social media posts.", "https://www.govtrack.us")
