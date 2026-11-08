import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import BusinessSettings
from core.permissions import perm_required

from . import services
from .forms import DesignUploadForm, FontUploadForm, OccasionForm, PosterForm
from .models import FontAsset, Occasion, Poster, PosterExport, PosterTemplate, TemplateField

PREVIEW_WIDTH = 720


def _designs_with_boxes():
    return PosterTemplate.objects.filter(is_active=True, versions__fields__isnull=False).distinct().order_by("name")


# --- Posters ---------------------------------------------------------------------------------

@require_GET
@perm_required("posters.view_poster")
def poster_list(request):
    today = timezone.localdate()
    return render(request, "posters/poster_list.html", {
        "posters": Poster.objects.select_related("template", "occasion").prefetch_related("exports")[:50],
        "upcoming": services.upcoming_occasions(today, days=45),
        "has_designs": PosterTemplate.objects.exists(),
        "has_starters": PosterTemplate.objects.filter(is_starter=True).exists(),
        "has_occasions": Occasion.objects.exists(),
    })


@require_POST
@perm_required("posters.add_postertemplate")
def starter_designs_create(request):
    created = services.create_starter_designs(request.user)
    if created:
        messages.success(request, f"Added {len(created)} starter designs. Pick one to make a poster.")
    else:
        messages.info(request, "The starter designs are already there.")
    return redirect(request.POST.get("next") or "posters:new")


@require_GET
@perm_required("posters.add_poster")
def poster_new(request):
    occasion = None
    if (occasion_id := request.GET.get("occasion", "")).isdigit():
        occasion = Occasion.objects.filter(pk=int(occasion_id)).first()
    return render(request, "posters/poster_new.html", {
        "designs": [(t, t.current_version) for t in _designs_with_boxes()],
        "occasion": occasion,
        "has_starters": PosterTemplate.objects.filter(is_starter=True).exists(),
    })


@require_POST
@perm_required("posters.add_poster")
def poster_create(request):
    template = get_object_or_404(_designs_with_boxes(), pk=request.POST.get("template") or 0)
    occasion = Occasion.objects.filter(pk=request.POST.get("occasion") or 0).first()
    try:
        poster = services.create_poster(template=template, occasion=occasion, user=request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("posters:new")
    return redirect("posters:edit", pk=poster.pk)


@require_http_methods(["GET", "POST"])
@perm_required("posters.change_poster")
def poster_edit(request, pk: int):
    poster = get_object_or_404(Poster.objects.select_related("template", "occasion"), pk=pk)
    version = poster.template.current_version
    initial = {"name": poster.name, "template": poster.template_id,
               **{f"v_{key}": value for key, value in poster.values.items()}}
    form = PosterForm(request.POST or None, version=version, initial=initial)
    if request.method == "POST" and form.is_valid():
        new_template = form.cleaned_data["template"]
        services.save_poster(poster, name=form.cleaned_data["name"], template=new_template,
                             values=form.values(), user=request.user)
        if new_template.pk != version.template_id:
            messages.info(request, "Design changed. Text in boxes both designs share has been kept.")
            return redirect("posters:edit", pk=poster.pk)
        if "export" in request.POST:
            if not request.user.has_perm("posters.add_posterexport"):
                messages.error(request, "You don't have permission to export posters.")
                return redirect("posters:edit", pk=poster.pk)
            export = services.export_poster(poster, request.user)
            messages.success(request, f"Exported “{poster.name}” (#{export.export_no}). It's kept under the poster's exports.")
            return redirect("posters:export", pk=export.pk)
        messages.success(request, "Poster saved.")
        return redirect("posters:edit", pk=poster.pk)
    return render(request, "posters/poster_edit.html", {
        "poster": poster, "form": form, "version": version,
        "exports": list(poster.exports.select_related("created_by")[:20]),
        "preview_url": reverse("posters:preview", args=[poster.pk]),
    })


@require_POST
@perm_required("posters.view_poster")
def poster_preview(request, pk: int):
    """The poster as it would export with the text currently typed (not saved).
    Same renderer as the export, shrunk for the screen."""
    poster = get_object_or_404(Poster, pk=pk)
    template = PosterTemplate.objects.filter(pk=request.POST.get("template") or poster.template_id).first() \
        or poster.template
    version = template.current_version
    submitted = {key[2:]: value for key, value in request.POST.items() if key.startswith("v_")}
    values = {**poster.values, **services.clean_values(version, submitted)}
    result = services.render_version(version, values)
    response = HttpResponse(services.to_bytes(result.image, max_width=PREVIEW_WIDTH, fmt="JPEG"),
                            content_type="image/jpeg")
    response["X-Poster-Warnings"] = json.dumps(result.warnings)
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
@perm_required("posters.view_poster")
def export_detail(request, pk: int):
    export = get_object_or_404(PosterExport.objects.select_related("poster", "version__template", "created_by"), pk=pk)
    settings = BusinessSettings.load()
    share = {
        "imageUrl": reverse("posters:export_image", args=[export.pk]),
        "filename": services.export_filename(export.poster, export.export_no),
        "subject": export.poster.name + (f" from {settings.name}" if settings.name else ""),
        "text": "", "whatsapp": "", "email": "",
    }
    return render(request, "posters/export_detail.html", {"export": export, "share": share})


@require_GET
@perm_required("posters.view_poster")
def export_image(request, pk: int):
    export = get_object_or_404(PosterExport.objects.select_related("poster"), pk=pk)
    response = FileResponse(export.file.open("rb"), content_type="image/png",
                            as_attachment=request.GET.get("download") == "1",
                            filename=services.export_filename(export.poster, export.export_no))
    response["Cache-Control"] = "private, no-store"
    return response


# --- Designs ---------------------------------------------------------------------------------

@require_GET
@perm_required("posters.view_postertemplate")
def design_list(request):
    templates = PosterTemplate.objects.prefetch_related("versions__fields").order_by("-is_active", "name")
    return render(request, "posters/design_list.html", {
        "designs": [(t, t.current_version) for t in templates],
        "has_starters": PosterTemplate.objects.filter(is_starter=True).exists(),
    })


@require_http_methods(["GET", "POST"])
@perm_required("posters.add_postertemplate")
def design_create(request):
    form = DesignUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            template = services.create_design(name=form.cleaned_data["name"], background=form.cleaned_data["background"],
                                              overlay=form.cleaned_data.get("overlay"), user=request.user)
        except ValidationError as exc:
            form.add_error("background", exc)
        else:
            messages.success(request, "Design uploaded. Now draw the boxes where the text and logo go.")
            return redirect("posters:design_edit", pk=template.pk)
    return render(request, "posters/design_form.html", {"form": form})


@require_http_methods(["GET", "POST"])
@perm_required("posters.change_postertemplate")
def design_edit(request, pk: int):
    template = get_object_or_404(PosterTemplate, pk=pk)
    version = template.current_version
    error = ""
    if request.method == "POST":
        raw = request.POST.get("fields_json", "")
        try:
            fields = services.parse_fields(raw, version)
            new_name = " ".join(request.POST.get("name", "").split())[:100]
            if new_name and new_name != template.name:
                template.name = new_name
                template.save(update_fields=["name", "updated_at"])
            saved = services.save_design_fields(template, fields, request.user)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            note = f" Posters already exported keep the earlier version; this is version {saved.version_no}." \
                if saved.pk != version.pk else ""
            messages.success(request, f"Design saved.{note}")
            return redirect("posters:design_edit", pk=template.pk)
    boxes = [
        {"key": f.key, "source": f.source, "label": f.label, "x": f.x, "y": f.y, "w": f.w, "h": f.h,
         "font": f.font_id, "size": f.size, "min_size": f.min_size, "color": f.color, "align": f.align,
         "valign": f.valign, "max_lines": f.max_lines, "line_spacing": float(f.line_spacing),
         "uppercase": f.uppercase, "fit": f.fit}
        for f in version.fields.all()
    ]
    services.ensure_bundled_fonts()
    return render(request, "posters/design_edit.html", {
        "template": template, "version": version, "error": error,
        "editor_data": {
            "width": version.width, "height": version.height, "background": version.background.url,
            "boxes": json.loads(request.POST["fields_json"]) if error and request.POST.get("fields_json", "").startswith("[") else boxes,
            "fonts": [{"id": f.pk, "name": f.name} for f in FontAsset.objects.all()],
            "sources": [{"value": s.value, "label": s.label, "image": s.value in TemplateField.IMAGE_SOURCES}
                        for s in TemplateField.Source],
            "aligns": TemplateField.Align.choices, "valigns": TemplateField.VAlign.choices,
            "fits": TemplateField.Fit.choices,
        },
    })


@require_GET
@perm_required("posters.view_postertemplate")
def design_sample(request, pk: int):
    """The design filled with placeholder text (for the design gallery)."""
    template = get_object_or_404(PosterTemplate, pk=pk)
    version = template.current_version
    result = services.render_version(version, services.sample_values(version))
    width = min(int(request.GET.get("w", "480")) if request.GET.get("w", "").isdigit() else 480, 1080)
    response = HttpResponse(services.to_bytes(result.image, max_width=width, fmt="JPEG"), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
@perm_required("posters.change_postertemplate")
def design_set_active(request, pk: int, active: bool):
    template = get_object_or_404(PosterTemplate, pk=pk)
    services.set_design_active(template, active, request.user)
    messages.success(request, f"“{template.name}” {'restored' if active else 'archived'}.")
    return redirect("posters:designs")


@require_http_methods(["GET", "POST"])
@perm_required("posters.view_fontasset")
def font_list(request):
    services.ensure_bundled_fonts()
    can_add = request.user.has_perm("posters.add_fontasset")
    form = FontUploadForm(request.POST or None, request.FILES or None) if can_add else None
    if request.method == "POST":
        if not can_add:
            messages.error(request, "You don't have permission to add fonts.")
            return redirect("posters:fonts")
        if form.is_valid():
            services.add_font(form.cleaned_data["name"], form.cleaned_data["file"], request.user)
            messages.success(request, "Font added. It's now in the design editor's font list.")
            return redirect("posters:fonts")
    return render(request, "posters/font_list.html", {"fonts": FontAsset.objects.all(), "form": form})


# --- Calendar --------------------------------------------------------------------------------

@require_GET
@perm_required("posters.view_occasion")
def occasion_list(request):
    today = timezone.localdate()
    rows = []
    for occasion in Occasion.objects.all():
        when = occasion.next_date(today)
        rows.append((occasion, when, (when - today).days if when else None))
    rows.sort(key=lambda r: (not r[0].is_active, r[1] is None, r[1] or today, r[0].name))
    return render(request, "posters/occasion_list.html", {"rows": rows, "has_any": bool(rows)})


@require_POST
@perm_required("posters.add_occasion")
def occasion_starters(request):
    added = services.add_starter_occasions(request.user)
    messages.success(request, f"Added {added} occasions. Check the dates and add your own." if added
                     else "Those occasions are already in your calendar.")
    return redirect("posters:calendar")


@require_http_methods(["GET", "POST"])
@perm_required("posters.change_occasion")
def occasion_edit(request, pk: int | None = None):
    occasion = get_object_or_404(Occasion, pk=pk) if pk else None
    if occasion is None and not request.user.has_perm("posters.add_occasion"):
        return redirect("posters:calendar")
    form = OccasionForm(request.POST or None, instance=occasion)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Saved “{saved.name}”.")
        return redirect("posters:calendar")
    return render(request, "posters/occasion_form.html", {"form": form, "occasion": occasion})
