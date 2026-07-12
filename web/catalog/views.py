from django.shortcuts import get_object_or_404, render

from accounts.decorators import role_required

from .models import SensorDesign


@role_required("guest")
def catalog_list_view(request):
    designs = SensorDesign.objects.prefetch_related("hardware_codes").all()
    return render(request, "catalog/list.html", {"designs": designs})


@role_required("guest")
def catalog_detail_view(request, slug):
    design = get_object_or_404(
        SensorDesign.objects.prefetch_related(
            "hardware_codes__revisions__firmwares", "hardware_codes__devices"
        ),
        slug=slug,
    )

    # Derive firmwares (latest per revision) and real devices from the linked
    # hardware codes — nothing is stored directly on the design.
    hardware = []
    for code in design.hardware_codes.all():
        revisions = []
        for rev in code.revisions.all():
            firmwares = sorted(
                rev.firmwares.all(), key=lambda f: f.uploaded_at, reverse=True
            )
            revisions.append({"rev": rev, "latest": firmwares[0] if firmwares else None})
        hardware.append(
            {
                "code": code,
                "revisions": revisions,
                "devices": list(code.devices.all()),
            }
        )

    return render(
        request,
        "catalog/detail.html",
        {"design": design, "hardware": hardware},
    )
