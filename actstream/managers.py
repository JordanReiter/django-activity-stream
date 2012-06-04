from django.db.models.query import QuerySet
from django.db.models import Manager
from django.db import connection
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.generic import GenericForeignKey
from django.conf import settings
from django.db import models

class GFKManager(Manager):
    """
    A manager that returns a GFKQuerySet instead of a regular QuerySet.

    """
    def get_query_set(self):
        return GFKQuerySet(self.model)

class GFKQuerySet(QuerySet):
    """
    A QuerySet with a fetch_generic_relations() method to bulk fetch
    all generic related items.  Similar to select_related(), but for
    generic foreign keys.

    Based on http://www.djangosnippets.org/snippets/984/
    Firstly improved at http://www.djangosnippets.org/snippets/1079/

    """
    def __init__(self, *args, **kwargs):
        try:
            app_label, model_name = settings.AUTH_PROFILE_MODULE.split('.')
            self.profile_module = models.get_model(app_label, model_name)._meta.module_name
        except AttributeError, inst:
            self.profile_module = None
        super(GFKQuerySet,self).__init__(*args, **kwargs)
    
    def fetch_generic_relations(self):
        qs = self._clone()

        #print "Just started: So far there are %d queries" % len(connection.queries)

        gfk_fields = [g for g in self.model._meta.virtual_fields if isinstance(g, GenericForeignKey)]
        
        ct_map = {}
        item_map = {}
        data_map = {}
        missing_records = {}
        
        for item in qs:
            for gfk in gfk_fields:
                ct_id_field = self.model._meta.get_field(gfk.ct_field).column
                #print "ct_id=%s" % getattr(item, ct_id_field)
                #print "item_id=%s" % getattr(item, gfk.fk_field)
                #print "%s %s" % (ct_id_field, getattr(item, ct_id_field))
                ct_map.setdefault(
                    (getattr(item, ct_id_field)), {}
                    )[getattr(item, gfk.fk_field)] = (gfk.name, item.id)
            item_map[item.id] = item

            #print "Looping through sq: So far there are %d queries" % len(connection.queries)


        #print "%s" % ct_map.items()

        for (ct_id), items_ in ct_map.items():
            if (ct_id):
                ct = ContentType.objects.get_for_id(ct_id)
                related_fields = ["user__pk"]
                if self.profile_module:
                    related_fields.append("user_%s__pk" % self.profile_module)
                for o in ct.model_class().objects.select_related(*related_fields).filter(id__in=items_.keys()):
                    (gfk_name, item_id) = items_[o.id]
                    data_map[(ct_id, o.id)] = o

                #print "Looping through ct_map.items(): So far there are %d queries" % len(connection.queries)

        for item in qs:
            for gfk in gfk_fields:
                #print "getattr(item, '%s', None)=%s" % (gfk.name, getattr(item, gfk.name, None))
                if (getattr(item, gfk.fk_field) != None):
                    ct_id_field = self.model._meta.get_field(gfk.ct_field).column
                    #print "%s" % type(self.model)
                    try:
                        setattr(item, gfk.name, data_map[(getattr(item, ct_id_field), getattr(item, gfk.fk_field))])
                    except KeyError:
                        if ((self.model._meta.get_field(gfk.ct_field).null or self.model._meta.get_field(gfk.ct_field).blank) and
                            (self.model._meta.get_field(gfk.fk_field).null or self.model._meta.get_field(gfk.fk_field).blank)
                        ):
                            setattr(item, gfk.name, None)
                        else:
                            missing_records.setdefault(
                                (gfk.ct_field, gfk.fk_field), {}
                                ).setdefault(getattr(item, ct_id_field),[]).append(getattr(item, gfk.fk_field))

                #print "Looping through qs: So far there are %d queries" % len(connection.queries)



        for flds, ct_items_ in missing_records.items():
            ct_field, fk_field = flds
            for ct, objs in ct_items_.items():
                qp = { "%s__pk" % ct_field: ct, "%s__in" % fk_field: objs }
                print "About to run qs.exclude(%s)" % (",".join(["%s=%s" % (k,v) for k,v in qp.items()]))
                qs &= qs.exclude(**qp)
        
        return qs
=======
from collections import defaultdict

from django.db import models
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType

from actstream.gfk import GFKManager
from actstream.decorators import stream


class ActionManager(GFKManager):
    """
    Default manager for Actions, accessed through Action.objects
    """

    def public(self, *args, **kwargs):
        """
        Only return public actions
        """
        kwargs['public'] = True
        return self.filter(*args, **kwargs)

    @stream
    def actor(self, object, **kwargs):
        """
        Stream of most recent actions where object is the actor.
        Keyword arguments will be passed to Action.objects.filter
        """
        return object.actor_actions.public(**kwargs)

    @stream
    def target(self, object, **kwargs):
        """
        Stream of most recent actions where object is the target.
        Keyword arguments will be passed to Action.objects.filter
        """
        return object.target_actions.public(**kwargs)

    @stream
    def action_object(self, object, **kwargs):
        """
        Stream of most recent actions where object is the action_object.
        Keyword arguments will be passed to Action.objects.filter
        """
        return object.action_object_actions.public(**kwargs)

    @stream
    def model_actions(self, model, **kwargs):
        """
        Stream of most recent actions by any particular model
        """
        ctype = ContentType.objects.get_for_model(model)
        return self.public(
            (Q(target_content_type=ctype) |
            Q(action_object_content_type=ctype) |
            Q(actor_content_type=ctype)),
            **kwargs
        )

    @stream
    def user(self, object, **kwargs):
        """
        Stream of most recent actions by objects that the passed User object is
        following.
        """
        from actstream.models import Follow
        q = Q()
        qs = self.filter(public=True)
        actors_by_content_type = defaultdict(lambda: [])
        others_by_content_type = defaultdict(lambda: [])

        follow_gfks = Follow.objects.filter(user=object).values_list(
            'content_type_id', 'object_id', 'actor_only')

        if not follow_gfks:
            return qs.none()

        for content_type_id, object_id, actor_only in follow_gfks.iterator():
            actors_by_content_type[content_type_id].append(object_id)
            if not actor_only:
                others_by_content_type[content_type_id].append(object_id)

        for content_type_id, object_ids in actors_by_content_type.iteritems():
            q = q | Q(
                actor_content_type=content_type_id,
                actor_object_id__in=object_ids,
            )
        for content_type_id, object_ids in others_by_content_type.iteritems():
            q = q | Q(
                target_content_type=content_type_id,
                target_object_id__in=object_ids,
            ) | Q(
                action_object_content_type=content_type_id,
                action_object_object_id__in=object_ids,
            )
        qs = qs.filter(q, **kwargs)
        return qs


class FollowManager(models.Manager):
    """
    Manager for Follow model.
    """

    def for_object(self, instance):
        """
        Filter to a specific instance.
        """
        content_type = ContentType.objects.get_for_model(instance).pk
        return self.filter(content_type=content_type, object_id=instance.pk)

    def is_following(self, user, instance):
        """
        Check if a user is following an instance.
        """
        if not user or user.is_anonymous():
            return False
        queryset = self.for_object(instance)
        return queryset.filter(user=user).exists()