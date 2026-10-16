"""Explicit application transport over pinned verified data; imports stay private."""
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass,field
import json,math,os,threading
from pathlib import Path
from types import MappingProxyType
import psycopg2
from psycopg2.pool import ThreadedConnectionPool


class RuntimeUnavailable(ValueError):pass


@dataclass(frozen=True)
class RuntimeConfig:
    connection:object=field(repr=False)
    expected_role:str
    dataset:object=field(repr=False)
    pool_size:int=4
    acquire_timeout:float=2
    statement_timeout_ms:int=30000

    def __post_init__(self):
        c=dict(self.connection);d=dict(self.dataset)
        if not {'host','port','dbname','user','sslmode'}<=set(c) or set(c)-{'host','port','dbname','user','sslmode','password','sslrootcert'}:raise ValueError('Explicit bounded connection fields required')
        if any(type(c[n]) is not str or not c[n] for n in('host','dbname','user','sslmode')):raise ValueError('Nonempty connection text required')
        if type(c['port']) is not int or not 1<=c['port']<=65535:raise ValueError('Invalid runtime port')
        local=c['host'].startswith('/') or c['host'] in('127.0.0.1','::1')
        if c['sslmode'] not in('disable','verify-full') or(not local and c['sslmode']!='verify-full'):raise ValueError('Remote transport requires verified TLS')
        if any(type(c[n]) is not str for n in('password','sslrootcert') if n in c):raise ValueError('Optional connection fields must be text')
        if type(self.expected_role) is not str or not self.expected_role:raise ValueError('Actual database role must be explicit')
        if set(d)!={'version','source_run_id','manifest_sha256','codebook_sha256','matches','held_count','ready','origin_target'} or d['version']!='cricstats_dataset_v1':raise ValueError('Pinned dataset identity required')
        for name in('manifest_sha256','codebook_sha256'):
            if type(d[name]) is not str or len(d[name])!=64 or any(x not in '0123456789abcdef' for x in d[name]):raise ValueError('Dataset hash invalid')
        if type(d['source_run_id']) is not str or not d['source_run_id']:raise ValueError('Source generation required')
        for name,low in(('matches',1),('held_count',0)):
            if type(d[name]) is not int or d[name]<low:raise ValueError('Dataset coverage invalid')
        if type(d['ready']) is not bool or not isinstance(d['origin_target'],Mapping):raise ValueError('Dataset origin/readiness invalid')
        origin=dict(d['origin_target'])
        if set(origin)!={'version','run_id','root','data_dir','socket_dir','port','user','database'} or origin['version']!='d3_descriptor_v1' or type(origin['run_id']) is not str:raise ValueError('Original import identity invalid')
        if type(self.pool_size) is not int or not 1<=self.pool_size<=6:raise ValueError('Pool size must be1..6')
        if type(self.acquire_timeout) not in(int,float) or not math.isfinite(self.acquire_timeout) or not 0<self.acquire_timeout<=10:raise ValueError('Bounded acquisition timeout required')
        if type(self.statement_timeout_ms) is not int or not 100<=self.statement_timeout_ms<=60000:raise ValueError('Bounded statement timeout required')
        object.__setattr__(self,'connection',MappingProxyType(c));d['origin_target']=MappingProxyType(origin);object.__setattr__(self,'dataset',MappingProxyType(d))


def load_runtime_config(path:Path)->RuntimeConfig:
    if not path.is_absolute() or path.is_symlink() or path.resolve()!=path:raise ValueError('Explicit canonical runtime config path required')
    st=path.stat()
    if st.st_uid!=os.getuid() or st.st_mode&0o077:raise ValueError('Runtime configuration must be private')
    try:
        raw=json.loads(path.read_text())
        if type(raw) is not dict or raw.pop('version',None)!='cricstats_runtime_v1':raise ValueError('Runtime configuration version invalid')
        return RuntimeConfig(**raw)
    except(TypeError,KeyError,AttributeError) as e:raise ValueError('Malformed runtime configuration') from e


def export_local_config(proof,path:Path):
    from db.source_connection import assert_proof_session
    from db.team_summary import assert_team_read
    from db.partnership_read import assert_partnership_read
    assert_proof_session(proof);a=assert_team_read(proof);assert_partnership_read(proof)
    raw={'version':'cricstats_runtime_v1','connection':{'host':proof.descriptor['socket_dir'],'port':proof.descriptor['port'],
      'dbname':proof.descriptor['database'],'user':proof.descriptor['user'],'sslmode':'disable'},'expected_role':proof.descriptor['user'],
      'dataset':{'version':'cricstats_dataset_v1','source_run_id':a['run_id'],'manifest_sha256':a['manifest_sha256'],'codebook_sha256':a['codebook_sha256'],
      'matches':a['matches'],'held_count':a['held_count'],'ready':a['ready'],'origin_target':dict(proof.descriptor)},
      'pool_size':4,'acquire_timeout':2,'statement_timeout_ms':30000}
    RuntimeConfig(**{k:v for k,v in raw.items() if k!='version'})
    if not path.is_absolute() or path.resolve()!=path or path.is_symlink():raise ValueError('Canonical config output required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(json.dumps(raw,indent=2)+'\n')


@dataclass(frozen=True)
class RuntimeSession:
    connection:object
    owner:object=field(repr=False)
    @property
    def descriptor(self):return self.owner.config.dataset['origin_target']


def assert_runtime_session(session):
    if type(session) is not RuntimeSession or session.owner.closed or session.owner._leases.get(id(session.connection)) is not session:raise RuntimeUnavailable('Active application lease required')
    config=session.owner.config;c=dict(config.connection);actual=session.connection.get_dsn_parameters()
    if any(actual.get(n)!=str(c[n]) for n in('host','port','dbname','user')):raise RuntimeUnavailable('Application connection target differs')
    with session.connection.cursor() as cur:
        cur.execute("SELECT current_database(),current_user,current_setting('transaction_read_only'),current_setting('transaction_isolation'),inet_server_addr(),COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)")
        db,role,readonly,isolation,address,ssl=cur.fetchone()
    if(db!=c['dbname'] or role!=config.expected_role or readonly!='on' or isolation!='repeatable read'):raise RuntimeUnavailable('Application transaction/role differs')
    if c['sslmode']=='verify-full' and not ssl:raise RuntimeUnavailable('Verified TLS connection required')
    if c['sslmode']=='disable' and address not in(None,'127.0.0.1','::1'):raise RuntimeUnavailable('Unencrypted nonlocal connection refused')


def verify_runtime_dataset(session,authority):
    expected=session.owner.config.dataset
    for key,fieldname in(('run_id','source_run_id'),('manifest_sha256','manifest_sha256'),('codebook_sha256','codebook_sha256'),('matches','matches'),('held_count','held_count'),('ready','ready')):
        if authority[key]!=expected[fieldname]:raise RuntimeUnavailable('Application dataset generation differs')


class RuntimePool:
    def __init__(self,config:RuntimeConfig,*,pool_factory=ThreadedConnectionPool):
        if not isinstance(config,RuntimeConfig):raise ValueError('Validated explicit runtime configuration required')
        self.config=config;self.closed=True;self._pool=None;self._factory=pool_factory;self._slots=threading.BoundedSemaphore(config.pool_size);self._lock=threading.RLock();self._leases={}
    def start(self):
        with self._lock:
            if not self.closed:return
            self._pool=self._factory(1,self.config.pool_size,**dict(self.config.connection),connect_timeout=5,application_name='cricstats-runtime');self.closed=False
    def close(self):
        with self._lock:
            self.closed=True
            if self._pool:self._pool.closeall();self._pool=None
    @contextmanager
    def session(self):
        if self.closed:raise RuntimeUnavailable('Application pool is stopped')
        if not self._slots.acquire(timeout=self.config.acquire_timeout):raise RuntimeUnavailable('Application pool is busy')
        conn=None;discard=False
        try:
            with self._lock:
                if self.closed:raise RuntimeUnavailable('Application pool is stopped')
                conn=self._pool.getconn()
            conn.rollback();conn.set_session(readonly=True,isolation_level='REPEATABLE READ',autocommit=False)
            with conn.cursor() as c:c.execute('SET LOCAL statement_timeout=%s;SET LOCAL jit=off',(self.config.statement_timeout_ms,))
            session=RuntimeSession(conn,self)
            with self._lock:self._leases[id(conn)]=session
            assert_runtime_session(session)
            yield session
        except(psycopg2.Error,OSError):discard=True;raise
        finally:
            try:
                if conn:
                    with self._lock:self._leases.pop(id(conn),None)
                    try:conn.rollback()
                    except(psycopg2.Error,OSError):discard=True
                    with self._lock:
                        if self._pool:self._pool.putconn(conn,close=discard or bool(conn.closed))
                        elif not conn.closed:conn.close()
            finally:self._slots.release()
