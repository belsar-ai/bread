Name:           bread
Version:        0.2.0
Release:        1%{?dist}
Summary:        Btrfs snapshot manager with CLI and GUI

License:        Apache-2.0
URL:            https://github.com/belsar-ai/bread
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-uv-build
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros

Requires:       python3-gobject
Requires:       btrfs-progs
Requires:       libadwaita
Requires:       gtk4
Requires:       polkit

%description
Bread is a Btrfs snapshot manager. It provides automatic hourly snapshots
with configurable retention, interactive rollback (CLI and GTK GUI), and
one-level undo. The CLI uses an fdisk-style command loop. The GUI elevates
via pkexec for privileged operations.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_unitdir}
install -m 644 data/bread-snapshot.service %{buildroot}%{_unitdir}/
install -m 644 data/bread-snapshot.timer %{buildroot}%{_unitdir}/

mkdir -p %{buildroot}%{_datadir}/polkit-1/actions
install -m 644 data/org.bread.policy %{buildroot}%{_datadir}/polkit-1/actions/

mkdir -p %{buildroot}%{_datadir}/applications
install -m 644 data/bread.desktop %{buildroot}%{_datadir}/applications/

%post
systemctl daemon-reload 2>/dev/null || :

%preun
%systemd_preun bread-snapshot.timer bread-snapshot.service

%postun
%systemd_postun bread-snapshot.timer

%files
%license LICENSE
%{_bindir}/bread
%{_bindir}/bread-gui
%{python3_sitelib}/bread/
%{python3_sitelib}/bread-%{version}.dist-info/
%{_unitdir}/bread-snapshot.service
%{_unitdir}/bread-snapshot.timer
%{_datadir}/polkit-1/actions/org.bread.policy
%{_datadir}/applications/bread.desktop

%changelog
* Thu Jun 26 2025 Belsar <belsar@users.noreply.github.com> - 0.1.0-1
- Initial package
