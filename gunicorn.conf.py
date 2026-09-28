# Um worker só, de propósito.
# Sessões e o contador de tentativas de login ficam na memória do processo.
# Com mais de um worker, o login cairia num processo e a página seguinte noutro.
bind = "127.0.0.1:8000"
workers = 1
threads = 2
worker_class = "gthread"
timeout = 30
accesslog = "-"
errorlog = "-"
capture_output = True
# Sem socket de controle: o systemd deixa o sistema de arquivos somente leitura
# e o www-data não tem um home gravável. O processo só precisa escutar em 127.0.0.1.
control_socket_disable = True
# O access log do Gunicorn registra método e caminho, não o corpo do POST.
# A senha não vai parar no log de acesso.
