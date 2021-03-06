'''
A simple & native python in-memory object collection.
Collections can be saved to and loaded from disk.
Objects are serialised as json documents.

SINGLE THREAD & SINGLE PROCESS

Author: Geoffroy Noel
'''
from builtins import object
from bson.objectid import ObjectId
from django.core.exceptions import ObjectDoesNotExist
from django.core.cache import cache
from . import utils as dbutils
from unsccore.dbackends.utils import json


class CollectionInsertedResponse(object):

    def __init__(self, inserted_id):
        self.inserted_id = inserted_id

# NOT WORKING with runserver for python 3
import atexit

atexit.register(lambda: Collection.save_collections())

# import signal
# #thread.enable_signals()
# signal.signal(signal.SIGINT, lambda: print('test'))

def get_collection_size_info(docs, serialised):
    return '%s things, %.4f MB' % (len(docs), len(serialised) / 1024.0 / 1024)


class Collection(object):

    _collections = {}

    @classmethod
    def save_collections(cls):
        #print('SAVE COLLECTIONS'); 
        [c.save() for c in Collection._collections.values()]
         
    @classmethod
    def get_collection(cls, key):
        ret = cls._collections.get(key)
        if ret is None:
            ret = cls._collections[key] = Collection(key)
        return ret

    def __init__(self, key):
        # TODO: use dynamic name for collection
        self.key = key
        if not self.lock():
            raise Exception(self.lock_error)
        self.load()
        
    def __del__(self):
        print('DEL')

    def lock(self):
        '''Returns False if another running thread is using this collection.
        Only one thread/process can change the collection at any time.
        If returns True this process/thread will lock the collection.
        '''
        ret = True
        import os
        pid = os.getpid()
        tid = dbutils.get_threadid()
        cid = id(self)
        ptcid = '%s:%s:%s' % (pid, tid, cid)
        ptcid_last = cache.get(self.key + '.ptid', '54321:1:1')
        if ptcid_last != ptcid:
            ret = False
            # last owner different from us, check if process still alive
            ptcid_last = ptcid_last.split(':')
            ret = not dbutils.is_process_active(ptcid_last[0])
        if ret:
            cache.set(self.key + '.ptid', ptcid)

        self.lock_error = 'Another process/thread is already using the collection (us: %s; them: %s)' % (
            ptcid, ptcid_last)

        return ret

    def save(self):
        content = json.dumps({str(k): dbutils.get_mongo_dict_from_model(model, plain_id=True) for k, model in self._id_docs.items()})
        cache.set(self.key, content)
        print('COLLECTION WRITTEN (%s)' %
              (get_collection_size_info(self._id_docs, content)))

    def load(self):
        from unsccore.mogels import MongoDocumentModule
        content = cache.get(self.key) or '{}'
        self._id_docs = {}
        for doc in json.loads(content).values():
            model = MongoDocumentModule.new(**doc)
            self._id_docs[model.pk] = model
        print('COLLECTION READ (%s)' % (get_collection_size_info(self._id_docs, content)))
    
    def find(self, query):
        #print(query)
        ret = []
        
        if len(query) == 1:
            m = self._id_docs.get(query.get('pk'))
            if m:
                return [m]
        
        # TODO: use views!
        for model in self._id_docs.values():

            match = 1
            for k, v in query.items():
                if getattr(model, k, None) != v:
                    match = 0
                    break

            if match:
                ret.append(model)

        return ret

    def insert_one(self, model):
        # TODO: check for duplicates
        model.pk = getattr(model, 'pk', None) or str(ObjectId())
        self._id_docs[str(model.pk)] = model

    def replace_one(self, model):
        # TODO: assume here we replace with full object
        self._id_docs[str(model.pk)] = model
#         for adoc in self.find(query):
#             adoc.update(model)

    def delete_one(self, model):
        if str(model.pk) in self._id_docs:
            del self._id_docs[str(model.pk)]


class MongoQuerySet(object):
    '''
    A pymongo query builder and cursor over a result
    that implements some of the django QuerySet interface.
    '''

    def __init__(self, doc_class):
        '''
        doc_class: MongoModel or subclass. Used as a default to instantiate
        a Mongo Document.
        '''
        self._collection = Collection.get_collection('things')
        self.doc_class = doc_class
        # a hash of the last query executed on Mongo by this QuerySet
        self.query_executed_hash = None
        self.reset_query()
        # Used to prevent expensive cloning when doing this:
        # Thing.objects.filter()
#         self._cloned = False

    def reset_query(self):
        self.query = {'filters': {}, 'order': None}

    def create_index(self, akeys, unique=False):
        pass

    def clone(self):
        ret = MongoQuerySet(self.doc_class)
        
        # Much faster deep copy than copy.deepcopy
        #ret.query = copy.deepcopy(self.query)
        q = self.query
        ret.query = {
            'filters': {k:v for k,v in q['filters'].items()},
            'order': [v for v in q['order'] or []]
        }
        
#         ret._cloned = True
        return ret

    def save(self):
        pass

    def load(self):
        pass

    def create(self, **kwargs):
        '''Django QuerySet method, returns a new, saved model.'''
        # TODO: unused?
        obj = self.doc_class.new(**kwargs)
        obj.save()
        return obj

    def all(self):
        '''Django QuerySet method, returns a COPY.'''
        # TODO: reset query?
        ret = self.clone()
        return ret

    def filter(self, **filters):
        '''Django QuerySet method, returns a COPY.'''
        #print(filters)
        ret = self.clone()
        if filters:
            ret.query['filters'].update(filters)
        return ret

    def first(self):
        '''Django QuerySet method, returns a model or None.'''
        try:
            for ret in self:
                return ret
        except StopIteration:
            return None

    def get(self, **filters):
        '''Django QuerySet method, returns a model.'''
        docs = self.filter(**filters)
        ret = docs.first()
        if ret is None:
            raise ObjectDoesNotExist('Thing not found (%s)' % repr(filters))

        return ret

    def count(self):
        '''Django QuerySet method, returns number of objects in queryset.'''
        return len(self._get_cursor())

    def order_by(self, *fields):
        '''Django QuerySet method, returns a COPY.'''
        self.query['order'] = fields
        return self

    def __iter__(self):
        return iter(self._get_cursor())
    
    def _get_collection(self):
        # TODO: cache?
        return self._collection

    def _get_cursor(self, reset=False):
        query_hash = json.dumps(self.query)
        if reset or query_hash != self.query_executed_hash:
            collection = self._get_collection()
            # TODO: query

            #filters = dbutils._get_mongo_dict_from_model_dict(self.query['filters'])
            # TODO: works for simple care field=value
            # but need to convert django operators to mongo

            # print 'MONGO FIND (%s)' % repr(filters)
            self.cursor = collection.find(self.query['filters'])
            # self.cursor.batch_size(100)

            if self.query['order']:
                # TODO
                orders = []
                for field in [self.query['order']]:
                    if field == 'pk':
                        field = '_id'
                    field_name = field.strip('-')
                    order = [field_name, 1]
                    if field_name != field:
                        order[1] = -1
                    orders.append(order)
                self.cursor.sort(orders)

            self.query_executed_hash = query_hash

        return self.cursor

    def _mongo_replace_one(self, model):
        collection = self._get_collection()
        #doc = model._get_mongo_dict()
        if getattr(model, 'pk'):
            collection.replace_one(model)
        else:
            collection.insert_one(model)

    def _mongo_delete_one(self, model):
        self._get_collection().delete_one(model)
