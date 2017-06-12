from django.contrib import admin
from django.conf import settings
from symposion.conference.models import Section

from symposion.proposals.models import ProposalBase
from symposion.schedule.models import Schedule, Day, Room, SlotKind, Slot, \
    SlotRoom, Presentation
from symposion.utils.mail import send_email

import datetime


admin.site.register(Schedule, list_display=("section", "published"))
admin.site.register(Day, list_display=("date", "schedule",))
admin.site.register(Room, list_display=("name", "schedule"))
admin.site.register(SlotKind, list_display=("label", "schedule"))
admin.site.register(SlotRoom, list_display=("slot", "room"))


class SlotAdmin(admin.ModelAdmin):
    list_display = ['day_schedule', 'day_date', 'start', 'end', 'kind', 'rooms']
    list_filter = ['kind', 'day__schedule']
    list_select_related = True
    ordering = ['day__date', 'start', 'end', 'slotroom__room__name']

    def rooms(self, obj):
        return ', '.join(obj.slotroom_set.values_list('room__name', flat=True))
    rooms.admin_order_field = 'slotroom__room__name'

    def day_date(self, obj):
        return obj.day.date
    day_date.admin_order_field = 'day__date'

    def day_schedule(self, obj):
        return obj.day.schedule.section.name
    day_schedule.admin_order_field = 'day__schedule__section__name'


admin.site.register(Slot, SlotAdmin)


class PresentationAdmin(admin.ModelAdmin):
    filter_horizontal = ['additional_speakers']
    list_display = (
        'number',
        'title',
        'speaker',
        'cancelled',
        'proposal_base',
        'kind',
        'section',
        'tutorial_attendees',
        'tutorial_max',
        'video_url',
        'slides_url',
        'assets_url'
    )
    list_filter = (
        'section',
        'proposal_base__kind',
        'cancelled',
    )
    search_fields = (
        'title',
        'speaker__name',
        'additional_speakers__name',
        'proposal_base__title',
        'proposal_base__kind__name',
        'description',
        'abstract',
        'section__name',
        'video_url',
        'assets_url',
        'slides_url',
    )

    def get_queryset(self, request):
        qs = super(PresentationAdmin, self).get_queryset(request)
        return qs.select_related('speaker', 'speaker__user')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'proposal_base':
            kwargs['queryset'] = ProposalBase.objects.order_by('title')
        if db_field.name == 'slot':
            kwargs['queryset'] = Slot.objects.order_by('day__date', 'start', 'end')
        if db_field.name == 'section':
            if 'queryset' not in kwargs:
                kwargs['queryset'] = Section.objects.all()
            kwargs['queryset'] = kwargs['queryset'].select_related('conference')
        return super(PresentationAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

    def number(self, presentation):
        return presentation.proposal_base.number
    number.admin_order_field = 'proposal_base__pk'

    def kind(self, presentation):
        return presentation.proposal_base.kind

    def tutorial_attendees(self, presentation):
        if hasattr(presentation.proposal, 'registration_count'):
            return presentation.proposal.registration_count
        else:
            return 'N/A'
    tutorial_attendees.short_description = 'Attendees'

    def tutorial_max(self, presentation):
        return getattr(presentation.proposal, 'max_attendees', 'N/A')
    tutorial_max.short_description = 'Attendees Max'

    # When a presentation is saved in the admin we send an email to the staff
    def save_model(self, request, obj, form, change):
        # First, save the object
        super(PresentationAdmin, self).save_model(request, obj, form, change)
        # Next, send an email to python staff
        now = datetime.datetime.now()
        send_email(
            [settings.REGISTRATION_EMAIL],
            "presentation_updated",
            context={"presentation": obj,
                     "time": now,
                     "user": request.user}
        )


admin.site.register(Presentation, PresentationAdmin)
