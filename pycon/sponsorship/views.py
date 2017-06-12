from cStringIO import StringIO
import itertools
import logging
import os
import time
from zipfile import ZipFile, ZipInfo

from constance import config

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render_to_response,\
    render
from django.template import RequestContext
from django.utils.translation import ugettext_lazy as _

from pycon.sponsorship.forms import SponsorApplicationForm, \
    SponsorBenefitsFormSet, SponsorDetailsForm, SponsorEmailForm
from pycon.sponsorship.models import Sponsor, SponsorBenefit, \
    SponsorLevel


log = logging.getLogger(__name__)


@login_required
def sponsor_apply(request):
    if request.method == "POST":
        form = SponsorApplicationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            form.save()
            return redirect("dashboard")
    else:
        form = SponsorApplicationForm(user=request.user)

    return render_to_response("sponsorship/apply.html", {
        "form": form,
    }, context_instance=RequestContext(request))


@login_required
def sponsor_detail(request, pk):
    sponsor = get_object_or_404(Sponsor, pk=pk)

    if not sponsor.active or sponsor.applicant != request.user:
        return redirect("sponsor_list")

    formset_kwargs = {
        "instance": sponsor,
        "queryset": SponsorBenefit.objects.filter(active=True)
    }

    if request.method == "POST":

        form = SponsorDetailsForm(request.POST, instance=sponsor)
        formset = SponsorBenefitsFormSet(request.POST, request.FILES, **formset_kwargs)

        if form.is_valid() and formset.is_valid():
            formset.save()
            form.save()

            messages.success(request, "Your sponsorship application has been submitted!")

            return redirect(request.path)
    else:
        form = SponsorDetailsForm(instance=sponsor)
        formset = SponsorBenefitsFormSet(**formset_kwargs)

    return render_to_response("sponsorship/detail.html", {
        "sponsor": sponsor,
        "form": form,
        "formset": formset,
    }, context_instance=RequestContext(request))


@staff_member_required
def sponsor_export_data(request):
    sponsors = []
    data = ""

    for sponsor in Sponsor.objects.order_by("added"):
        d = {
            "name": sponsor.name,
            "url": sponsor.external_url,
            "level": (sponsor.level.order, sponsor.level.name),
            "description": "",
        }
        for sponsor_benefit in sponsor.sponsor_benefits.all():
            if sponsor_benefit.benefit_id == 2:
                d["description"] = sponsor_benefit.text
        sponsors.append(d)

    def izip_longest(*args):
        fv = None

        def sentinel(counter=([fv]*(len(args)-1)).pop):
            yield counter()
        iters = [itertools.chain(it, sentinel(), itertools.repeat(fv)) for it in args]
        try:
            for tup in itertools.izip(*iters):
                yield tup
        except IndexError:
            pass

    def pairwise(iterable):
        a, b = itertools.tee(iterable)
        b.next()
        return izip_longest(a, b)

    def level_key(s):
        return s["level"]

    for level, level_sponsors in itertools.groupby(sorted(sponsors, key=level_key), level_key):
        data += "%s\n" % ("-" * (len(level[1])+4))
        data += "| %s |\n" % level[1]
        data += "%s\n\n" % ("-" * (len(level[1])+4))
        for sponsor, next in pairwise(level_sponsors):
            description = sponsor["description"].strip()
            description = description if description else "-- NO DESCRIPTION FOR THIS SPONSOR --"
            data += "%s\n\n%s" % (sponsor["name"], description)
            if next is not None:
                data += "\n\n%s\n\n" % ("-"*80)
            else:
                data += "\n\n"

    return HttpResponse(data, content_type="text/plain;charset=utf-8")


def _get_benefit_filenames(sponsor, benefit_name):
    """
    Given a sponsor and one benefit name, return a list of the absolute
    paths of the files that exist for that sponsor's benefit of that name.
    """
    paths = []
    if benefit_name == 'Web logo':
        if sponsor.web_logo:
            paths = [sponsor.web_logo.path]
    else:
        benefits = SponsorBenefit.objects.filter(sponsor=sponsor,
                                                 benefit__name=benefit_name,
                                                 active=True)\
                                         .exclude(upload='')
        paths = [
            sponsor_benefit.upload.path
            for sponsor_benefit in benefits
        ]
    return [path for path in paths if os.path.exists(path)]


@staff_member_required
def sponsor_zip_logo_files(request):
    """Return a zip file of sponsor web and print logos"""

    zip_stringio = StringIO()
    with ZipFile(zip_stringio, "w") as zipfile:
        for benefit_name, dir_name in (("Web logo", "web_logos"),
                                       ("Print logo", "print_logos"),
                                       ("Advertisement", "advertisement")):
            for level in SponsorLevel.objects.all():
                level_name = level.name.lower().replace(" ", "_")
                for sponsor in Sponsor.objects.filter(level=level, active=True):
                    sponsor_name = sponsor.name.lower().replace(" ", "_")
                    full_dir = "/".join([dir_name, level_name, sponsor_name])
                    paths = _get_benefit_filenames(sponsor, benefit_name)
                    for path in paths:
                        modtime = time.gmtime(os.stat(path).st_mtime)
                        with open(path, "rb") as f:
                            fname = os.path.basename(path)
                            zipinfo = ZipInfo(filename=full_dir + "/" + fname,
                                              date_time=modtime)
                            zipfile.writestr(zipinfo, f.read())

    response = HttpResponse(zip_stringio.getvalue(),
                            content_type="application/zip")
    prefix = settings.CONFERENCE_URL_PREFIXES[settings.CONFERENCE_ID]
    response['Content-Disposition'] = \
        'attachment; filename="pycon_%s_sponsorlogos.zip"' % prefix
    return response


def email_selected_sponsors_action(modeladmin, request, queryset, form=None):
    """Action invoked from admin to email selected sponsors"""
    # Too bad `request` isn't the first parameter, we could use the same
    # function for both admin action and view.
    # But hey, we don't really need to do the work here...
    pks = ",".join([str(pk) for pk in queryset.values_list('pk', flat=True)])
    return sponsor_email(request, pks)
email_selected_sponsors_action.short_description = _(u"Email selected sponsors")


@staff_member_required
def sponsor_email(request, pks):
    sponsors = Sponsor.objects.filter(pk__in=pks.split(","))

    address_list = []
    for sponsor in sponsors:
        for email in sponsor.contact_emails:
            if email.lower() not in address_list:
                address_list.append(email.lower())
        if sponsor.applicant.email.lower() not in address_list:
            address_list.append(sponsor.applicant.email.lower())

    initial = {
        'from_': config.SPONSOR_FROM_EMAIL,
    }

    # Note: on initial entry, we've got the request from the admin page,
    # which was actually a POST, but not from our page. So be careful to
    # check if it's a POST and it looks like our form.
    if request.method == 'POST' and 'subject' in request.POST:
        form = SponsorEmailForm(request.POST, initial=initial)
        is_valid = form.is_valid()

        # If the user has just edited the Subject or Body, then show
        # them what it will look like after the %% substitutions are
        # done.  Only once they like the look of the output, and
        # re-submit the form without any changes, do we hit Send.
        if is_valid:
            data = form.cleaned_data
            sponsor = sponsors[0]  # any old sponsor will do
            subject = sponsor.render_email(data['subject'])
            body = sponsor.render_email(data['body'])
            is_valid = (
                subject == data['sample_subject']
                and
                body == data['sample_body']
            )
            print repr(subject)
            print repr(data['sample_subject'])
            form.data = form.data.copy()
            form.data['sample_subject'] = subject
            form.data['sample_body'] = body

        if is_valid:
            # Send emails one at a time, rendering the subject and
            # body as templates.
            for sponsor in sponsors:
                address_list = []
                for email in sponsor.contact_emails:
                    if email.lower() not in address_list:
                        address_list.append(email.lower())
                if sponsor.applicant.email.lower() not in address_list:
                    address_list.append(sponsor.applicant.email.lower())

                subject = sponsor.render_email(data['subject'])
                body = sponsor.render_email(data['body'])

                mail = EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=data['from_'],
                    to=address_list,
                    cc=data['cc'].split(","),
                    bcc=data['bcc'].split(",")
                )
                mail.send()
            messages.add_message(request, messages.INFO, _(u"Email sent to sponsors"))
            return redirect(reverse('admin:sponsorship_sponsor_changelist'))
    else:
        form = SponsorEmailForm(initial=initial)
    context = {
        'address_list': address_list,
        'form': form,
        'pks': pks,
        'sponsors': sponsors,
    }
    return render(request, "sponsorship/email.html", context)
