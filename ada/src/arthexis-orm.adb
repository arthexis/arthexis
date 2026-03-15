with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;

package body Arthexis.ORM is

   function Connect_SQLite (Path : String) return Database_Connection is
   begin
      return (URI => To_Unbounded_String ("sqlite://" & Path));
   end Connect_SQLite;

   procedure Execute (Conn : in out Database_Connection; SQL : String) is
      pragma Unreferenced (Conn);
      pragma Unreferenced (SQL);
   begin
      --  Wire this to GNATCOLL.SQL.Exec in runtime integration.
      null;
   end Execute;

   procedure Register_SQL_Function
     (Conn      : in out Database_Connection;
      Name      : String;
      Arity     : Natural;
      SQL_Body  : String) is
      pragma Unreferenced (Name);
      pragma Unreferenced (Arity);
   begin
      Execute (Conn, SQL_Body);
   end Register_SQL_Function;

   procedure Register_Trigger
     (Conn      : in out Database_Connection;
      Name      : String;
      SQL_Body  : String) is
      pragma Unreferenced (Name);
   begin
      Execute (Conn, SQL_Body);
   end Register_Trigger;

end Arthexis.ORM;
