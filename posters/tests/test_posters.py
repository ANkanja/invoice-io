"""Posters: drawing, starter designs, your own designs and boxes, versions,
exports, the calendar, and the pages."""

import datetime
import io
import json
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import ImmutableRecordError
from core.testing import configure_business, make_staff, make_user
from posters import services
from posters.models import BUNDLED_FONTS_DIR, FontAsset, Occasion, Poster, PosterExport, PosterTemplate
from posters.rendering import Box, layout_text, render_poster, wrap

FONT = str(BUNDLED_FONTS_DIR / "Montserrat_400Regular.ttf")


def png(width=1080, height=1080, color=(240, 240, 240, 255), mode="RGB") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color[:3] if mode == "RGB" else color).save(buffer, "PNG")
    return SimpleUploadedFile("design.png", buffer.getvalue(), content_type="image/png")


class MediaTestCase(TestCase):
    """Uploaded and exported files go to a temporary folder, removed afterwards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp(prefix="invoicing-test-posters-")
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()


class RenderingTests(TestCase):
    def box(self, **kw):
        return Box(**{"key": "headline", "label": "Headline", "x": 10, "y": 10, "w": 400, "h": 120,
                      "font_path": FONT, "size": 60, "min_size": 20, "max_lines": 2, **kw})

    def test_wraps_words_and_breaks_overlong_words(self):
        from posters.rendering import _font
        font = _font(FONT, 40)
        lines = wrap(["Wash your hands with soap"], font, 300)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(font.getlength(line) <= 300 for line in lines))
        self.assertTrue(all(font.getlength(line) <= 120 for line in wrap(["Supercalifragilistic"], font, 120)))

    def test_text_shrinks_to_fit_then_is_cut_short(self):
        fits = layout_text("Global Handwashing Day", self.box())
        self.assertTrue(fits.fits)
        self.assertLess(fits.font.size, 60)  # had to shrink to fit two lines
        too_long = layout_text(" ".join(["words"] * 80), self.box())
        self.assertFalse(too_long.fits)
        self.assertEqual(too_long.font.size, 20)
        self.assertLessEqual(len(too_long.lines), 2)
        self.assertTrue(too_long.lines[-1].endswith("…"))

    def test_bullets_and_capitals(self):
        layout = layout_text("- clean\n* dry", self.box(uppercase=True, max_lines=4, h=400, size=30))
        self.assertEqual(layout.lines, ["• CLEAN", "• DRY"])

    def test_render_keeps_size_draws_text_and_warns(self):
        background = Image.new("RGB", (500, 300), "white")
        result = render_poster(background, [self.box()], {"headline": "Hello"})
        self.assertEqual(result.image.size, (500, 300))
        self.assertEqual(result.warnings, [])
        self.assertNotEqual(result.image.getbbox(), None)
        self.assertLess(min(result.image.crop((10, 10, 410, 130)).convert("L").getdata()), 100)  # dark text drawn
        warned = render_poster(background, [self.box()], {"headline": " ".join(["long"] * 100)})
        self.assertEqual(len(warned.warnings), 1)
        self.assertIn("Headline", warned.warnings[0])

    def test_rendering_is_repeatable(self):
        background = Image.new("RGB", (500, 300), "#E3EFEB")
        one = render_poster(background, [self.box()], {"headline": "Same text"}).image.tobytes()
        two = render_poster(background, [self.box()], {"headline": "Same text"}).image.tobytes()
        self.assertEqual(one, two)

    def test_logo_fits_inside_its_box(self):
        background = Image.new("RGB", (400, 400), "white")
        logo = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
        box = Box(key="logo", label="Logo", x=100, y=100, w=100, h=100, is_image=True)
        image = render_poster(background, [box], {}, {"logo": logo}).image
        self.assertEqual(image.getpixel((150, 150)), (255, 0, 0))   # centre of the box: logo
        self.assertEqual(image.getpixel((150, 110)), (255, 255, 255))  # 2:1 logo fitted: top of box empty
        self.assertEqual(image.getpixel((50, 50)), (255, 255, 255))   # outside the box untouched


class StarterAndCalendarTests(MediaTestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(name="Test Business", phone="0700 000000", email="info@example.com")

    def test_starter_designs_are_added_once_with_their_boxes(self):
        created = services.create_starter_designs(self.user)
        self.assertEqual(len(created), 4)
        self.assertEqual(services.create_starter_designs(self.user), [])
        sizes = sorted((t.current_version.width, t.current_version.height) for t in created)
        self.assertEqual(sizes, [(1080, 1080), (1080, 1080), (1080, 1920), (1080, 1920)])
        for template in created:
            keys = set(template.current_version.fields.values_list("key", flat=True))
            self.assertEqual(keys, {"date", "headline", "message", "logo", "business_name", "contacts"})
        self.assertEqual(FontAsset.objects.filter(bundled_file__endswith=".ttf").count(), 3)

    def test_occasion_fills_a_new_poster(self):
        services.create_starter_designs(self.user)
        occasion = Occasion.objects.create(name="Global Handwashing Day", month=10, day=15,
                                           headline="Clean hands", message="- Use soap")
        template = PosterTemplate.objects.get(name="Green band (square)")
        poster = services.create_poster(template=template, occasion=occasion, user=self.user)
        self.assertEqual(poster.values["headline"], "Clean hands")
        self.assertEqual(poster.values["message"], "- Use soap")
        self.assertEqual(poster.values["date"], "15 October")
        self.assertEqual(poster.values["business_name"], "Test Business")
        self.assertEqual(poster.values["contacts"], "0700 000000  ·  info@example.com")

    def test_next_date_and_upcoming(self):
        today = datetime.date(2026, 11, 10)
        yearly = Occasion.objects.create(name="World Toilet Day", month=11, day=19)
        passed = Occasion.objects.create(name="Madaraka Day", month=6, day=1)
        once = Occasion.objects.create(name="Easter 2026", month=4, day=5, year=2026)
        leap = Occasion(name="Leap day", month=2, day=29)
        hidden = Occasion.objects.create(name="Hidden", month=11, day=20, is_active=False)
        self.assertEqual(yearly.next_date(today), datetime.date(2026, 11, 19))
        self.assertEqual(passed.next_date(today), datetime.date(2027, 6, 1))
        self.assertIsNone(once.next_date(today))
        self.assertEqual(leap.next_date(today), datetime.date(2027, 2, 28))
        self.assertEqual(leap.date_text(), "29 February")
        upcoming = services.upcoming_occasions(today, days=30)
        self.assertEqual([(o.name, days) for o, _when, days in upcoming], [("World Toilet Day", 9)])
        self.assertNotIn(hidden, [o for o, _w, _d in upcoming])

    def test_starter_calendar_adds_only_missing_occasions(self):
        Occasion.objects.create(name="World AIDS Day", month=12, day=1, headline="My own wording")
        added = services.add_starter_occasions(self.user)
        self.assertGreater(added, 10)
        self.assertEqual(services.add_starter_occasions(self.user), 0)
        self.assertEqual(Occasion.objects.get(name="World AIDS Day").headline, "My own wording")
        self.assertEqual(Occasion.objects.get(name="Global Handwashing Day").date_text(), "15 October")


class DesignAndExportTests(MediaTestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(name="Test Business")
        services.ensure_bundled_fonts()
        self.font = FontAsset.objects.get(name="Montserrat Regular")

    def boxes_json(self, **override):
        box = {"key": "headline", "source": "headline", "label": "Headline", "x": 50, "y": 50, "w": 600, "h": 200,
               "font": self.font.pk, "size": 60, "min_size": 30, "color": "#112233", "align": "center",
               "valign": "middle", "max_lines": 2, "line_spacing": 1.1, "uppercase": False}
        box.update(override)
        logo = {"key": "logo", "source": "logo", "label": "Logo", "x": 10, "y": 900, "w": 150, "h": 150,
                "fit": "contain"}
        return json.dumps([box, logo])

    def design(self):
        return services.create_design(name="My design", background=png(), user=self.user)

    def test_own_design_size_is_read_from_the_picture(self):
        template = self.design()
        version = template.current_version
        self.assertEqual((version.version_no, version.width, version.height), (1, 1080, 1080))
        self.assertFalse(version.fields.exists())
        with self.assertRaises(ValidationError):  # too small
            services.create_design(name="Tiny", background=png(100, 100), user=self.user)
        with self.assertRaises(ValidationError):  # overlay of another size
            services.create_design(name="Mismatch", background=png(), overlay=png(1080, 1920, mode="RGBA"),
                                   user=self.user)

    def test_boxes_are_checked(self):
        version = self.design().current_version
        fields = services.parse_fields(self.boxes_json(), version)
        self.assertEqual([f["key"] for f in fields], ["headline", "logo"])
        self.assertEqual(fields[0]["font"], self.font)
        bad = [
            ("not json", None), (self.boxes_json(x=1075), "between"), (self.boxes_json(w=5), "between"),
            (self.boxes_json(font=None), "font"), (self.boxes_json(color="red"), "colour"),
            (self.boxes_json(source="price"), "choose what goes in it"), (self.boxes_json(key="Bad Key"), "small letters"),
            (self.boxes_json(key="logo"), "Two boxes"),
        ]
        for raw, message in bad:
            with self.subTest(raw=raw[:40]), self.assertRaises(ValidationError) as ctx:
                services.parse_fields(raw, version)
            if message:
                self.assertIn(message, ctx.exception.messages[0])

    def test_export_locks_the_version_and_editing_then_makes_a_new_one(self):
        template = self.design()
        services.save_design_fields(template, services.parse_fields(self.boxes_json(), template.current_version), self.user)
        poster = services.create_poster(template=template, user=self.user)
        services.save_poster(poster, name="Notice", template=template, values={"headline": "Hello"}, user=self.user)
        first = services.export_poster(poster, self.user)
        self.assertEqual((first.export_no, first.width, first.height), (1, 1080, 1080))
        self.assertEqual(first.values, {"headline": "Hello"})
        self.assertTrue(first.version.is_locked)
        with Image.open(first.file.path) as picture:
            self.assertEqual(picture.size, (1080, 1080))
        # Editing the design now makes version 2; the export still points at version 1.
        v2 = services.save_design_fields(template, services.parse_fields(self.boxes_json(size=80), template.current_version),
                                         self.user)
        self.assertEqual(v2.version_no, 2)
        self.assertEqual(v2.background.name, first.version.background.name)
        self.assertEqual(first.version.fields.get(key="headline").size, 60)
        second = services.export_poster(poster, self.user)
        self.assertEqual((second.export_no, second.version, first.file.name != second.file.name), (2, v2, True))
        # Unlocked versions are edited in place.
        v2_again = services.save_design_fields(template, services.parse_fields(self.boxes_json(), v2), self.user)
        self.assertEqual(v2_again.version_no, 3)  # v2 was locked by the second export

    def test_exports_cannot_be_changed_or_deleted(self):
        template = self.design()
        services.save_design_fields(template, services.parse_fields(self.boxes_json(), template.current_version), self.user)
        export = services.export_poster(services.create_poster(template=template, user=self.user), self.user)
        with self.assertRaises(ImmutableRecordError):
            export.save()
        with self.assertRaises(ImmutableRecordError):
            export.delete()

    def test_design_without_boxes_cannot_be_used(self):
        with self.assertRaises(ValidationError):
            services.create_poster(template=self.design(), user=self.user)


class PosterPageTests(MediaTestCase):
    def setUp(self):
        self.user = make_user()
        configure_business(name="Test Business", phone="0700 000000")
        self.client.force_login(self.user)

    def test_empty_page_offers_starter_designs_then_full_flow(self):
        r = self.client.get(reverse("posters:list"))
        self.assertContains(r, "Add the starter designs")
        self.client.post(reverse("posters:starter_designs"))
        self.assertEqual(PosterTemplate.objects.count(), 4)
        occasion = Occasion.objects.create(name="World Toilet Day", month=11, day=19)
        r = self.client.get(reverse("posters:new"), {"occasion": occasion.pk})
        self.assertContains(r, "World Toilet Day")
        template = PosterTemplate.objects.get(name="Fresh card (story)")
        r = self.client.post(reverse("posters:create"), {"template": template.pk, "occasion": occasion.pk})
        poster = Poster.objects.get()
        self.assertRedirects(r, reverse("posters:edit", args=[poster.pk]))
        self.assertEqual(poster.values["headline"], "World Toilet Day")
        # Live preview: same renderer, smaller JPEG, unsaved text.
        r = self.client.post(reverse("posters:preview", args=[poster.pk]),
                             {"template": template.pk, "v_headline": "Typed but not saved"})
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(json.loads(r["X-Poster-Warnings"]), [])
        with Image.open(io.BytesIO(r.content)) as preview:
            self.assertEqual(preview.size, (720, 1280))
        poster.refresh_from_db()
        self.assertEqual(poster.values["headline"], "World Toilet Day")
        # Save and export.
        data = {"name": "Toilet day 2026", "template": template.pk, "v_headline": "Clean toilets",
                "v_message": "- Flush\n- Wash hands", "v_date": "19 November", "v_business_name": "Test Business",
                "v_contacts": "0700 000000", "export": "1"}
        r = self.client.post(reverse("posters:edit", args=[poster.pk]), data)
        export = PosterExport.objects.get()
        self.assertRedirects(r, reverse("posters:export", args=[export.pk]))
        self.assertEqual(export.values["headline"], "Clean toilets")
        page = self.client.get(reverse("posters:export", args=[export.pk]))
        self.assertContains(page, 'data-share="whatsapp"')
        self.assertContains(page, 'id="share-data"')
        download = self.client.get(reverse("posters:export_image", args=[export.pk]), {"download": "1"})
        self.assertEqual(download["Content-Type"], "image/png")
        self.assertIn('attachment; filename="toilet-day-2026-1.png"', download["Content-Disposition"])

    def test_design_upload_and_box_editor(self):
        r = self.client.post(reverse("posters:design_create"), {"name": "Canva notice", "background": png()})
        template = PosterTemplate.objects.get(name="Canva notice")
        self.assertRedirects(r, reverse("posters:design_edit", args=[template.pk]))
        page = self.client.get(reverse("posters:design_edit", args=[template.pk]))
        data = page.context["editor_data"]
        self.assertEqual((data["width"], data["height"], data["boxes"]), (1080, 1080, []))
        font = FontAsset.objects.get(name="Montserrat ExtraBold")
        boxes = [{"key": "headline", "source": "headline", "label": "Headline", "x": 40, "y": 40, "w": 1000,
                  "h": 200, "font": font.pk, "size": 80, "min_size": 40, "color": "#1F6F5C", "align": "left",
                  "valign": "top", "max_lines": 2, "line_spacing": 1.1, "uppercase": False}]
        r = self.client.post(reverse("posters:design_edit", args=[template.pk]),
                             {"fields_json": json.dumps(boxes), "name": "Canva notice"})
        self.assertRedirects(r, reverse("posters:design_edit", args=[template.pk]))
        self.assertEqual(template.current_version.fields.count(), 1)
        boxes[0]["w"] = 5000  # off the picture: refused, nothing changed
        r = self.client.post(reverse("posters:design_edit", args=[template.pk]), {"fields_json": json.dumps(boxes)})
        self.assertContains(r, "Not saved")
        self.assertEqual(template.current_version.fields.get().w, 1000)
        sample = self.client.get(reverse("posters:design_sample", args=[template.pk]), {"w": "300"})
        self.assertEqual(sample["Content-Type"], "image/jpeg")

    def test_font_upload_checks_the_file(self):
        r = self.client.post(reverse("posters:fonts"), {"name": "Not a font",
                                                         "file": SimpleUploadedFile("x.ttf", b"not a font")})
        self.assertContains(r, "isn&#x27;t a font")
        with open(FONT, "rb") as fh:
            r = self.client.post(reverse("posters:fonts"), {"name": "My font",
                                                             "file": SimpleUploadedFile("mine.ttf", fh.read())})
        self.assertRedirects(r, reverse("posters:fonts"))
        self.assertTrue(FontAsset.objects.filter(name="My font").exclude(file="").exists())

    def test_calendar_pages(self):
        self.client.post(reverse("posters:calendar_starters"))
        self.assertTrue(Occasion.objects.filter(name="World Hand Hygiene Day", month=5, day=5).exists())
        r = self.client.post(reverse("posters:occasion_create"), {"name": "Open day", "day": "31", "month": "4",
                                                                  "headline": "", "message": "", "is_active": "on"})
        self.assertContains(r, "April has 30 days")
        r = self.client.post(reverse("posters:occasion_create"), {"name": "Open day", "day": "30", "month": "4",
                                                                  "headline": "Visit us", "message": "", "is_active": "on"})
        self.assertRedirects(r, reverse("posters:calendar"))
        self.assertContains(self.client.get(reverse("posters:calendar")), "Open day")

    def test_dashboard_and_sidebar_show_posters(self):
        today = datetime.date.today()
        soon = today + datetime.timedelta(days=3)
        Occasion.objects.create(name="Soon day", month=soon.month, day=soon.day)
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "Posters coming up")
        self.assertContains(r, "Soon day")
        self.assertContains(r, reverse("posters:list"))

    def test_staff_without_permission_get_403(self):
        staff = make_staff("viewer", perms=("posters.view_poster",))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("posters:list")).status_code, 200)
        for url in [reverse("posters:new"), reverse("posters:designs"), reverse("posters:calendar"),
                    reverse("posters:design_create"), reverse("posters:fonts")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("posters:starter_designs")).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        r = self.client.get(reverse("posters:list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])
